from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path


def native_sync(descriptor: int) -> int:
    try:
        os.fdatasync(descriptor)
    except OSError as error:
        if error.errno is None:
            raise
        return error.errno
    return 0


def exercise(directory: Path, mode: str) -> dict[str, int | str]:
    if os.geteuid() == 0 or mode not in {"exhaust", "recover"}:
        raise ValueError("probe_context")
    path = directory / "allocation"
    flags = os.O_WRONLY | os.O_NOFOLLOW
    flags |= os.O_CREAT | os.O_EXCL if mode == "exhaust" else os.O_APPEND
    descriptor = os.open(path, flags, 0o600)
    try:
        chunk = bytes(65536)
        written = 0
        write_errno = 0
        if mode == "exhaust":
            written = os.write(descriptor, chunk)
            if written != len(chunk) or native_sync(descriptor) != 0:
                raise RuntimeError("positive_control_failed")
        maximum = 16 * 1024 * 1024 if mode == "exhaust" else len(chunk)
        while written < maximum:
            try:
                count = os.write(descriptor, chunk)
            except OSError as error:
                if error.errno is None:
                    raise
                write_errno = error.errno
                break
            if count <= 0:
                raise RuntimeError("write_no_progress")
            written += count
        sync_errno = native_sync(descriptor)
        info = os.fstatvfs(descriptor)
        free_bytes = info.f_bavail * info.f_frsize
        if mode == "exhaust" and (write_errno != errno.EDQUOT or free_bytes < 16 * 1024 * 1024):
            raise RuntimeError("quota_not_demonstrated")
        if mode == "recover" and (write_errno != 0 or sync_errno != 0 or written != len(chunk)):
            raise RuntimeError("recovery_not_demonstrated")
        return {"mode": mode, "write_errno": write_errno, "sync_errno": sync_errno, "written": written, "free_bytes": free_bytes}
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise ValueError("arguments")
    sys.stdout.write(json.dumps(exercise(Path(sys.argv[1]), sys.argv[2])) + "\n")
