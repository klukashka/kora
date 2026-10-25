import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from kora.conformance.capture import Capture
from kora.executor.entry import Executor, ExecutorSettings
from kora.executor.launcher import ProcessLauncher, StepLauncher
from kora.plugins.vcs.fake import DirVcs, store
from kora.plugins.vcs.fake.provider import HELPER
from kora.plugins.vcs.local import LocalConfiguration
from kora.protocols.time import Clock
from tests.plugins.vcs.git_support import (
    BINDING,
    GONE_BINDING,
    GONE_REPO,
    PROVIDER_INSTANCE,
    REPO,
    STABLE_ID,
    ManualClock,
    VcsWorld,
)

MISSING_REVISION: Final[str] = "1" * 64
SEED_FILES: Final[dict[str, tuple[str, bytes]]] = {
    "a.txt": (store.REGULAR, b"alpha\n"),
    "keep/old.txt": (store.REGULAR, b"old\n"),
    "run.sh": (store.EXECUTABLE, b"#!/bin/sh\n"),
}
DirWorld = VcsWorld[DirVcs]


def empty_store(path: Path) -> None:
    for section in (*store.SECTIONS, "heads"):
        (path / section).mkdir(parents=True)


def put(path: Path, name: str, data: bytes) -> None:
    target = path / name
    if not target.exists():
        target.write_bytes(data)


def stored_revision(
    remote: Path,
    files: Mapping[str, tuple[str, bytes]],
    parent: str | None,
    message: str,
) -> str:
    entries: list[store.Entry] = []
    for path, (mode, data) in files.items():
        sha = store.digest_of(data)
        put(remote / "blobs", sha, data)
        entries.append(store.Entry(path, mode, len(data), sha))
    tree_data = store.tree_document(entries)
    tree = store.digest_of(tree_data)
    put(remote / "trees", tree, tree_data)
    document = store.revision_document(parent, message, tree)
    identifier = store.digest_of(document)
    put(remote / "revisions", identifier, document)
    return identifier


def set_head(remote: Path, branch: str, revision: str) -> None:
    target = remote / "heads" / branch
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(revision, encoding="ascii")


def candidate_commit(candidate: Path, message: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-I", HELPER, "commit", "--message", message],
        cwd=candidate,
        env={},
        capture_output=True,
        check=True,
    )
    return completed.stdout.decode("ascii")


@dataclass(frozen=True)
class DirBackend:
    remote: Path
    missing_revision: str = MISSING_REVISION

    def commit(self, candidate: Path, message: str, author: str) -> str:
        return candidate_commit(candidate, message)

    def remote_head(self, branch: str) -> str | None:
        head = self.remote / "heads" / branch
        return head.read_text(encoding="ascii") if head.is_file() else None


def configuration(remote: Path, gone: Path) -> LocalConfiguration:
    principal: dict[str, object] = {
        "provider_instance": PROVIDER_INSTANCE,
        "stable_id": STABLE_ID,
        "display_name": "kora local publisher",
    }

    def remote_entry(path: Path, binding: str) -> dict[str, object]:
        return {
            "path": str(path),
            "binding": binding,
            "binding_revision": 1,
            "credential_mode": "local_credential_free",
            "principal": principal,
        }

    return LocalConfiguration.model_validate(
        {
            "remotes": {
                REPO: remote_entry(remote, BINDING),
                GONE_REPO: remote_entry(gone, GONE_BINDING),
            },
            "tool_path": os.environ["PATH"].split(":"),
        }
    )


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
    provider_type: type[DirVcs] = DirVcs,
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
        DirBackend(remote),
    )
