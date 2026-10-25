import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from kora.conformance.capture import Capture
from kora.conformance.vcs import (
    Checkout,
    Kept,
    Prepared,
    authorization,
    grant,
    pinned,
    publish_request,
    sealed_input,
)
from kora.executor.entry import PublishOutcome, PublishPermit
from kora.executor.scan import InspectionSource
from kora.plugins.vcs.fake import DirVcs, store
from kora.plugins.vcs.local import LocalConfiguration
from kora.protocols.vcs import PlanStep, PublishPlan
from tests.plugins.vcs.dirvcs_support import DirWorld, make_dir_world
from tests.plugins.vcs.git_support import PUBLISH_BRANCH

CYCLES: int = 2
DEADLINE_SECONDS: float = 30.0


@dataclass
class Gate:
    reached: asyncio.Event | None = None


class HeldSource(InspectionSource):
    def __init__(self, inner: InspectionSource, reached: asyncio.Event) -> None:
        self._inner = inner
        self._reached = reached

    async def index(self, location: str, limit: int) -> bytes:
        return await self._inner.index(location, limit)

    async def content(self, location: str, limit: int) -> AsyncIterator[bytes]:
        async for block in self._inner.content(location, limit):
            yield block
            self._reached.set()
            await asyncio.Event().wait()

    async def revision_bytes(self, revision: str, limit: int) -> bytes:
        return await self._inner.revision_bytes(revision, limit)

    async def reachable(
        self, revision: str, base: str, timeout_ns: int
    ) -> frozenset[str]:
        return await self._inner.reachable(revision, base, timeout_ns)


class HeldScanVcs(DirVcs):
    def __init__(self, configuration: LocalConfiguration, gate: Gate) -> None:
        super().__init__(configuration)
        self.gate = gate

    def open(
        self, prepared_location: str
    ) -> AbstractAsyncContextManager[InspectionSource]:
        return self.held(prepared_location)

    @asynccontextmanager
    async def held(self, prepared_location: str) -> AsyncGenerator[InspectionSource]:
        async with self.sources.open(prepared_location) as source:
            reached = self.gate.reached
            yield source if reached is None else HeldSource(source, reached)


@dataclass
class Bench:
    world: DirWorld
    gate: Gate = field(default_factory=Gate)


def descriptors() -> int:
    return len(os.listdir("/dev/fd"))


def no_children() -> bool:
    try:
        os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        return True
    return False


def temporary_files(world: DirWorld) -> list[str]:
    return sorted(
        str(path.relative_to(world.remote))
        for path in world.remote.rglob("*")
        if path.name.startswith(".tmp-")
    )


def prepared_directories(world: DirWorld) -> int:
    return len(list(world.root.glob("bench-*/case-*/publication/prepared-*")))


async def cancelled_when(
    work: Coroutine[object, object, object], condition: Callable[[], bool]
) -> bool:
    task = asyncio.create_task(work)
    deadline = time.monotonic() + DEADLINE_SECONDS
    while not (condition() or task.done()):
        if time.monotonic() > deadline:
            raise TimeoutError
        await asyncio.sleep(0.01)
    reached = condition()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return reached
    return False


def changed_checkout(world: DirWorld, cycle: int) -> Checkout:
    checkout = world.checkout()
    world.commit(checkout, {f"cycle-{cycle}.txt": f"cycle {cycle}\n"}, "cycle")
    return checkout


def with_import_input(plan: PublishPlan, input_dir: Path) -> PublishPlan:
    def redirected(step: PlanStep) -> PlanStep:
        if "import" not in step.argv:
            return step
        argv = tuple(
            str(input_dir) if item == "{input_location}" else item for item in step.argv
        )
        return step.model_copy(update={"argv": argv})

    return plan.model_copy(
        update={"prepare": tuple(redirected(step) for step in plan.prepare)}
    )


async def prepared(
    world: DirWorld, checkout: Checkout, input_dir: Path | None = None
) -> Prepared:
    context = world.context()
    remote = world.remote_fixture()
    plan, sealed = await sealed_input(
        world.provider, world.executor, remote, checkout, context
    )
    if input_dir is not None:
        plan = with_import_input(plan, input_dir)
    result = await world.executor.prepare_and_inspect(
        plan,
        sealed,
        checkout.workspace(),
        publish_request(remote, checkout),
        pinned(world.binding()),
        checkout.areas,
        (),
        (),
        Kept(),
        context,
    )
    return Prepared(checkout, plan, sealed, result)


async def published(world: DirWorld, done: Prepared) -> PublishOutcome:
    return await world.executor.publish(
        done.result,
        done.plan,
        authorization(done.result),
        grant(),
        PublishPermit(done.result.publication.operation_id),
        world.context(),
    )


def blocked_input(world: DirWorld, cycle: int) -> Path:
    directory = world.root / f"blocked-input-{cycle}"
    directory.mkdir()
    (directory / "head.json").write_text(json.dumps({"head": "e" * 64, "bundle": True}))
    os.mkfifo(directory / "head.bundle")
    return directory


def success(bench: Bench, cycle: int) -> bool:
    world = bench.world
    (world.remote / "heads" / PUBLISH_BRANCH).unlink(missing_ok=True)
    checkout = changed_checkout(world, cycle)

    async def cycle_work() -> PublishOutcome:
        return await published(world, await prepared(world, checkout))

    return asyncio.run(cycle_work()).state == "pushed"


def killed_mid_import(bench: Bench, cycle: int) -> bool:
    world = bench.world
    checkout = changed_checkout(world, cycle)
    blocked = blocked_input(world, cycle)
    return asyncio.run(
        cancelled_when(
            prepared(world, checkout, blocked),
            lambda: any(
                world.root.glob(
                    "bench-*/case-*/publication/prepared-*/store/incoming-*"
                )
            ),
        )
    )


def killed_mid_publish(bench: Bench, cycle: int) -> bool:
    world = bench.world
    checkout = changed_checkout(world, cycle)
    done = asyncio.run(prepared(world, checkout))
    blob = store.digest_of(f"cycle {cycle}\n".encode())
    held = Path(done.result.revision.location) / store.PREPARED_STORE / "blobs" / blob
    held.unlink()
    os.mkfifo(held)
    return asyncio.run(
        cancelled_when(
            published(world, done),
            lambda: any((world.remote / "blobs").glob(".tmp-*")),
        )
    )


def cancelled_scan(bench: Bench, cycle: int) -> bool:
    checkout = changed_checkout(bench.world, cycle)

    async def cycle_work() -> bool:
        reached = asyncio.Event()
        bench.gate.reached = reached
        try:
            return await cancelled_when(prepared(bench.world, checkout), reached.is_set)
        finally:
            bench.gate.reached = None

    return asyncio.run(cycle_work())


ENDINGS: dict[str, tuple[Callable[[Bench, int], bool], int]] = {
    "success": (success, CYCLES),
    "killed_mid_import": (killed_mid_import, 0),
    "killed_mid_publish": (killed_mid_publish, CYCLES),
    "cancelled_scan": (cancelled_scan, CYCLES),
}


@pytest.mark.parametrize("ending", sorted(ENDINGS))
def test_dirvcs_publication_releases_resources(
    tmp_path: Path, kora_capture: Capture, ending: str
) -> None:
    gate = Gate()
    world = make_dir_world(
        tmp_path, kora_capture, provider_type=lambda config: HeldScanVcs(config, gate)
    )
    bench = Bench(world, gate)
    cycle_ending, kept = ENDINGS[ending]
    baseline = descriptors()

    reached = [cycle_ending(bench, cycle) for cycle in range(CYCLES)]

    assert reached == [True] * CYCLES
    assert descriptors() == baseline
    assert no_children()
    assert temporary_files(world) == []
    assert prepared_directories(world) == kept
