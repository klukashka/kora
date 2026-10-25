import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from kora.conformance.capture import Capture
from kora.conformance.vcs import (
    Checkout,
    Prepared,
    checked_out,
    prepared_from,
    published,
    sealed_input,
)
from kora.executor.failures import ExecutorError
from kora.executor.scan import InspectionSource
from kora.plugins.vcs.fake import DirVcs, store
from kora.plugins.vcs.local import LocalConfiguration
from kora.protocols.vcs import PlanStep, PublishPlan
from tests.plugins.vcs.dirvcs_support import DirWorld, candidate_commit, make_dir_world
from tests.plugins.vcs.git_support import PUBLISH_BRANCH, REPO

CYCLES: int = 2
DEADLINE_SECONDS: float = 30.0
Hold = Literal["content", "reachable"]


@dataclass
class Gate:
    hold: Hold | None = None
    reached: asyncio.Event | None = None


class HeldSource(InspectionSource):
    def __init__(self, inner: InspectionSource, gate: Gate) -> None:
        self._inner = inner
        self._gate = gate

    async def index(self, location: str, limit: int) -> bytes:
        return await self._inner.index(location, limit)

    async def content(self, location: str, limit: int) -> AsyncIterator[bytes]:
        async for block in self._inner.content(location, limit):
            yield block
            if self._gate.hold == "content" and self._gate.reached is not None:
                self._gate.reached.set()
                await asyncio.Event().wait()

    async def revision_bytes(self, revision: str, limit: int) -> bytes:
        return await self._inner.revision_bytes(revision, limit)

    async def reachable(
        self, revision: str, base: str, timeout_ns: int
    ) -> frozenset[str]:
        held = self._gate.hold == "reachable"
        return await self._inner.reachable(revision, base, 1 if held else timeout_ns)


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
            yield HeldSource(source, self.gate)


@dataclass
class Bench:
    world: DirWorld
    gate: Gate


def descriptors() -> int:
    return len(os.listdir("/dev/fd"))


def no_children() -> bool:
    try:
        os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        return True
    return False


def leftover_files(world: DirWorld) -> list[str]:
    stores = world.root.glob("bench-*/case-*/publication/prepared-*/store")
    found = [
        path
        for root in (world.remote, *stores)
        for path in root.rglob("*")
        if path.name.startswith((".tmp-", "incoming-"))
    ]
    return sorted(str(path.relative_to(world.root)) for path in found)


def prepared_directories(world: DirWorld) -> int:
    return len(list(world.root.glob("bench-*/case-*/publication/prepared-*")))


async def until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + DEADLINE_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError
        await asyncio.sleep(0.01)


async def cancelled_when(
    work: Awaitable[object], condition: Callable[[], bool]
) -> bool:
    task = asyncio.ensure_future(work)
    await until(lambda: condition() or task.done())
    reached = condition()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return reached
    return False


async def changed_checkout(world: DirWorld, cycle: int) -> Checkout:
    checkout = await checked_out(
        world.provider,
        world.executor,
        world.bench().areas(),
        REPO,
        world.base,
        world.context(),
    )
    candidate = world.candidate(checkout)
    (candidate / f"cycle-{cycle}.txt").write_text(f"cycle {cycle}\n")
    candidate_commit(candidate, "cycle")
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
    return await prepared_from(
        world.executor, remote, world.binding(), checkout, plan, sealed, context
    )


def blocked_input(world: DirWorld, cycle: int) -> Path:
    directory = world.root / f"blocked-input-{cycle}"
    directory.mkdir()
    (directory / "head.json").write_text(json.dumps({"head": "e" * 64, "bundle": True}))
    os.mkfifo(directory / "head.bundle")
    return directory


async def success(bench: Bench, cycle: int) -> bool:
    world = bench.world
    (world.remote / "heads" / PUBLISH_BRANCH).unlink(missing_ok=True)
    checkout = await changed_checkout(world, cycle)
    done = await prepared(world, checkout)
    return (await published(world.executor, done, world.context())).state == "pushed"


async def killed_mid_import(bench: Bench, cycle: int) -> bool:
    world = bench.world
    checkout = await changed_checkout(world, cycle)
    return await cancelled_when(
        prepared(world, checkout, blocked_input(world, cycle)),
        lambda: any(
            world.root.glob("bench-*/case-*/publication/prepared-*/store/incoming-*")
        ),
    )


async def killed_mid_publish(bench: Bench, cycle: int) -> bool:
    world = bench.world
    done = await prepared(world, await changed_checkout(world, cycle))
    blob = store.digest_of(f"cycle {cycle}\n".encode())
    held = Path(done.result.revision.location) / store.PREPARED_STORE / "blobs" / blob
    held.unlink()
    os.mkfifo(held)
    return await cancelled_when(
        published(world.executor, done, world.context()),
        lambda: any((world.remote / "blobs").glob(".tmp-*")),
    )


async def cancelled_scan(bench: Bench, cycle: int) -> bool:
    checkout = await changed_checkout(bench.world, cycle)
    reached = asyncio.Event()
    bench.gate.hold, bench.gate.reached = "content", reached
    try:
        return await cancelled_when(prepared(bench.world, checkout), reached.is_set)
    finally:
        bench.gate.hold, bench.gate.reached = None, None


async def timed_out_reachable(bench: Bench, cycle: int) -> bool:
    checkout = await changed_checkout(bench.world, cycle)
    bench.gate.hold = "reachable"
    try:
        await prepared(bench.world, checkout)
    except ExecutorError as error:
        return error.code == "inspection_source_unavailable"
    finally:
        bench.gate.hold = None
    return False


ENDINGS: dict[str, tuple[Callable[[Bench, int], Awaitable[bool]], int]] = {
    "success": (success, CYCLES),
    "killed_mid_import": (killed_mid_import, 0),
    "killed_mid_publish": (killed_mid_publish, CYCLES),
    "cancelled_scan": (cancelled_scan, CYCLES),
    "timed_out_reachable": (timed_out_reachable, CYCLES),
}


async def cycles(bench: Bench, ending: str) -> tuple[list[bool], bool]:
    cycle_ending, _ = ENDINGS[ending]
    baseline = descriptors()
    reached = [await cycle_ending(bench, cycle) for cycle in range(CYCLES)]
    try:
        await until(lambda: descriptors() == baseline)
    except TimeoutError:
        return reached, False
    return reached, True


@pytest.mark.parametrize("ending", sorted(ENDINGS))
def test_dirvcs_publication_releases_resources(
    tmp_path: Path, kora_capture: Capture, ending: str
) -> None:
    gate = Gate()
    world = make_dir_world(
        tmp_path, kora_capture, provider_type=lambda config: HeldScanVcs(config, gate)
    )
    baseline = descriptors()

    reached, released_in_loop = asyncio.run(cycles(Bench(world, gate), ending))

    assert reached == [True] * CYCLES
    assert released_in_loop
    assert descriptors() == baseline
    assert no_children()
    assert leftover_files(world) == []
    assert prepared_directories(world) == ENDINGS[ending][1]
