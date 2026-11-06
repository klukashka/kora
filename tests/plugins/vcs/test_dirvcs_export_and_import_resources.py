import os
from pathlib import Path

import pytest

from kora.plugins.vcs.fake import store


def test_export_walk_handles_deep_paths_with_bounded_descriptors(
    tmp_path: Path,
) -> None:
    root = os.open(tmp_path, store.DIRECTORY_FLAGS)
    current = os.dup(root)
    depth = 1100
    built = 0
    try:
        for _ in range(depth):
            os.mkdir("a", dir_fd=current)
            following = os.open("a", store.DIRECTORY_FLAGS, dir_fd=current)
            os.close(current)
            current = following
            built += 1
        descriptor = os.open("file", store.NEW_FLAGS, 0o600, dir_fd=current)
        os.close(descriptor)
        baseline = len(os.listdir("/dev/fd"))

        found = list(store.working_files(root))

        assert found == ["a/" * depth + "file"]
        assert len(os.listdir("/dev/fd")) == baseline
    finally:
        os.unlink("file", dir_fd=current)
        for _ in range(built):
            parent = os.open("..", store.DIRECTORY_FLAGS, dir_fd=current)
            os.close(current)
            current = parent
            os.rmdir("a", dir_fd=current)
        os.close(current)
        os.close(root)


def test_export_walk_bounds_empty_directory_entries(tmp_path: Path) -> None:
    for index in range(store.MAX_TREE_ENTRIES + 1):
        (tmp_path / str(index)).mkdir()
    root = os.open(tmp_path, store.DIRECTORY_FLAGS)
    baseline = len(os.listdir("/dev/fd"))

    try:
        with pytest.raises(store.Refused):
            list(store.working_files(root))
    finally:
        os.close(root)

    assert len(os.listdir("/dev/fd")) == baseline - 1


def test_import_closes_chunk_when_scratch_creation_is_refused(tmp_path: Path) -> None:
    (tmp_path / "tree.0.bin").write_bytes(b"data")
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    source = os.open(tmp_path, store.DIRECTORY_FLAGS)
    target = os.open(blobs, store.DIRECTORY_FLAGS)
    entry = store.Entry("file", store.REGULAR, 4, store.digest_of(b"data"))
    record = store.SealedRecord(entry, 0, 0)
    baseline = len(os.listdir("/dev/fd"))
    blobs.chmod(0o500)

    try:
        with pytest.raises(PermissionError):
            store.import_sealed(source, record, target, set())
        assert len(os.listdir("/dev/fd")) == baseline
    finally:
        blobs.chmod(0o700)
        store.discard_all()
        os.close(target)
        os.close(source)

    assert list(blobs.iterdir()) == []
