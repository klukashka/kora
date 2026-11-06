import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from kora.conformance.capture import Capture
from kora.conformance.common import CONFORMANCE_COMPONENT
from kora.conformance.vcs import (
    Checkout,
    Kept,
    Prepared,
    VcsBench,
    VcsBinding,
    VcsRemote,
    checked_out,
    pinned,
    prepared_from,
    publish_request,
    published,
    run,
    sealed_input,
)
from kora.executor.authorization import TrustedBinding
from kora.executor.entry import Executor, ExecutorSettings, PublishOutcome
from kora.executor.launcher import ProcessLauncher, StepLauncher
from kora.plugins.vcs.fake import DirVcs, store
from kora.plugins.vcs.local import LocalConfiguration
from kora.protocols.activity import NoActivity
from kora.protocols.common import ProtocolContext
from kora.protocols.deadlines import ExecutionDeadline
from kora.protocols.secret_value import SecretValue
from kora.protocols.time import Clock
from tests.plugins.vcs.dirvcs_checkout_support import (
    configuration,
    empty_store,
    set_head,
    stored_revision,
)
from tests.plugins.vcs.git_support import (
    BINDING,
    COMMIT_MESSAGE,
    GONE_REPO,
    PUBLISH_BRANCH,
    REPO,
    ManualClock,
    World,
    make_world,
)

__all__ = ["configuration", "empty_store", "stored_revision", "set_head"]

MISSING_REVISION: Final[str] = "1" * 64
SEED_FILES: Final[dict[str, tuple[str, bytes]]] = {
    "a.txt": (store.REGULAR, b"alpha\n"),
    "keep/old.txt": (store.REGULAR, b"old\n"),
    "run.sh": (store.EXECUTABLE, b"#!/bin/sh\n"),
}


@dataclass
class DirWorld:
    root: Path
    home: Path
    remote: Path
    seed: Path
    base: str
    provider: DirVcs
    executor: Executor
    capture: Capture
    clock: Clock
    cases: list[int] = field(default_factory=lambda: [0])

    def context(self, deadline: ExecutionDeadline | None = None) -> ProtocolContext:
        return ProtocolContext(
            namespace_id="namespace",
            run_id="run-1",
            attempt_id="conformance-attempt",
            logger=self.capture.diagnostics.logger(CONFORMANCE_COMPONENT),
            secrets=None,
            secret_admission=self.capture.scope.admission(),
            request_digest="sealed-request",
            deadline=deadline,
            clock=self.clock,
            activity=NoActivity(),
        )

    def bench(self) -> VcsBench:
        self.cases[0] += 1
        bench_root = self.root / f"bench-{self.cases[0]}"
        bench_root.mkdir()
        return VcsBench(self.executor, bench_root)

    def remote_fixture(self) -> VcsRemote:
        return VcsRemote(
            repo=REPO,
            revision=self.base,
            missing_revision=MISSING_REVISION,
            unreachable_repo=GONE_REPO,
            publish_branch=PUBLISH_BRANCH,
            commit_message=COMMIT_MESSAGE,
        )

    def binding(self) -> VcsBinding:
        return VcsBinding(
            TrustedBinding(
                binding=BINDING,
                binding_revision=1,
                credential_mode="local_credential_free",
                principal=self.provider.remotes[REPO].principal,
            )
        )

    def checkout(
        self, revision: str | None = None, ctx: ProtocolContext | None = None
    ) -> Checkout:
        return run(
            checked_out(
                self.provider,
                self.executor,
                self.bench().areas(),
                REPO,
                revision or self.base,
                ctx or self.context(),
            )
        )

    def candidate(self, checkout: Checkout) -> Path:
        candidate = checkout.areas.candidate
        assert candidate is not None
        return candidate

    def prepare(
        self,
        checkout: Checkout,
        secrets: tuple[SecretValue, ...] = (),
        protected: tuple[str, ...] = (),
        ctx: ProtocolContext | None = None,
    ) -> Prepared:
        context = ctx or self.context()
        remote = self.remote_fixture()
        plan, sealed = run(
            sealed_input(self.provider, self.executor, remote, checkout, context)
        )
        if not secrets and not protected:
            return run(
                prepared_from(
                    self.executor,
                    remote,
                    self.binding(),
                    checkout,
                    plan,
                    sealed,
                    context,
                )
            )
        result = run(
            self.executor.prepare_and_inspect(
                plan,
                sealed,
                checkout.workspace(),
                publish_request(remote, checkout),
                pinned(self.binding()),
                checkout.areas,
                secrets,
                protected,
                Kept(),
                context,
            )
        )
        return Prepared(checkout, plan, sealed, result)

    def publish(self, prepared: Prepared) -> PublishOutcome:
        return run(published(self.executor, prepared, self.context()))

    def remote_head(self, branch: str = PUBLISH_BRANCH) -> str | None:
        head = self.remote / "heads" / branch
        return head.read_text(encoding="ascii") if head.is_file() else None


def seeded_store(root: Path) -> tuple[Path, str]:
    remote = root / "remote.dirvcs"
    empty_store(remote)
    base = stored_revision(remote, SEED_FILES, None, "seed")
    set_head(remote, "main", base)
    return remote, base


def make_dir_world(
    root: Path,
    capture: Capture,
    launcher: StepLauncher | None = None,
    settings: ExecutorSettings | None = None,
    clock: Clock | None = None,
    provider_type: Callable[[LocalConfiguration], DirVcs] = DirVcs,
) -> DirWorld:
    root = root.resolve()
    home = root / "home"
    home.mkdir()
    remote, base = seeded_store(root)
    gone = root / "gone.dirvcs"
    empty_store(gone)
    provider = provider_type(configuration(remote, gone))
    shutil.rmtree(gone)
    executor = Executor(
        provider.tools,
        launcher or ProcessLauncher(),
        provider,
        provider,
        provider,
        settings,
    )
    return DirWorld(
        root,
        home,
        remote,
        remote,
        base,
        provider,
        executor,
        capture,
        clock or ManualClock(),
    )


AnyWorld = World | DirWorld
WorldFactory = Callable[[Path, Capture, StepLauncher | None], AnyWorld]
WORLDS: Final[dict[str, WorldFactory]] = {
    "git": make_world,
    "directory": make_dir_world,
}
