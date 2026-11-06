import json
import shutil
from pathlib import Path

import pytest

from kora.conformance.capture import Capture
from kora.executor.failures import ExecutorError
from kora.ir.node import ErrorClass
from kora.plugins.vcs.fake import store
from kora.protocols.errors import KoraProtocolError
from tests.plugins.vcs.dirvcs_support import (
    empty_store,
    make_dir_world,
    set_head,
    stored_revision,
)
from tests.plugins.vcs.git_support import PUBLISH_BRANCH, write


@pytest.mark.parametrize("leg", ["decoy", "different", "ancestor", "replaced"])
def test_directory_publication_destination_is_trusted(
    tmp_path: Path, kora_capture: Capture, leg: str
) -> None:
    world = make_dir_world(tmp_path, kora_capture)
    decoy = tmp_path / "decoy"
    empty_store(decoy)
    checkout = world.checkout()
    candidate = world.candidate(checkout)
    write(candidate, {"a.txt": "changed\n"})
    expected = None
    if leg == "decoy":
        (candidate / ".kora" / "repository.json").write_text(
            json.dumps({"path": str(decoy), "device": 0, "inode": 0})
        )
        (candidate / "redirect").symlink_to(decoy)
    elif leg in ("different", "ancestor"):
        expected = (
            world.base
            if leg == "ancestor"
            else stored_revision(
                world.remote, {"other": (store.REGULAR, b"other")}, world.base, "other"
            )
        )
        set_head(world.remote, PUBLISH_BRANCH, expected)
    else:
        world.remote.rename(tmp_path / "old-remote")
        shutil.copytree(tmp_path / "old-remote", world.remote)

    if leg == "replaced":
        with pytest.raises(KoraProtocolError) as raised:
            world.prepare(checkout)
        assert raised.value.code == "vcs_destination_changed"
    else:
        prepared = world.prepare(checkout)
        outcome = world.publish(prepared)
        assert outcome.state == ("pushed" if leg == "decoy" else "conflict")
        assert world.remote_head() == (
            prepared.result.publication.commit if leg == "decoy" else expected
        )
        assert prepared.result.publication.base_commit == world.base
    assert list((decoy / "heads").iterdir()) == []


@pytest.mark.parametrize("leg", ["protected_deletion", "secret_path"])
def test_directory_uncommitted_changes_are_inspected(
    tmp_path: Path, kora_capture: Capture, leg: str
) -> None:
    world = make_dir_world(tmp_path, kora_capture)
    checkout = world.checkout()
    candidate = world.candidate(checkout)
    secret = world.capture.scope.admit("kora-directory-secret-filename")
    if leg == "protected_deletion":
        (candidate / "keep" / "old.txt").unlink()
    else:
        shutil.copyfile(
            candidate / "a.txt", candidate / "kora-directory-secret-filename"
        )

    with pytest.raises(ExecutorError) as raised:
        world.prepare(checkout, secrets=(secret,), protected=("keep",))

    assert raised.value.failure_class is (
        ErrorClass.PROTECTED_PATH_MODIFIED
        if leg == "protected_deletion"
        else ErrorClass.CREDENTIAL_LEAK
    )
    assert raised.value.code == (
        "protected_path_modified" if leg == "protected_deletion" else "secret_leak"
    )
    assert world.remote_head() is None


def test_directory_export_rejects_special_files(
    tmp_path: Path, kora_capture: Capture
) -> None:
    import os

    world = make_dir_world(tmp_path, kora_capture)
    checkout = world.checkout()
    os.mkfifo(world.candidate(checkout) / "special")

    with pytest.raises(ExecutorError) as raised:
        world.prepare(checkout)

    assert raised.value.failure_class is ErrorClass.ARTIFACT_INVALID
    assert world.remote_head() is None
