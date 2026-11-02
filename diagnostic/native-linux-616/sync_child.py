from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path


def exercise(path: Path, native_sync_number: int) -> None:
    if sys.platform != "linux" or os.geteuid() == 0:
        raise ValueError("requires_unprivileged_linux")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    entered: queue.Queue[tuple[int, int]] = queue.Queue(maxsize=1)
    completed = threading.Event()
    outcomes: queue.Queue[dict[str, int]] = queue.Queue(maxsize=1)

    def writer() -> None:
        try:
            if os.write(descriptor, bytes(65536)) != 65536:
                raise RuntimeError("short_write")
            started = time.monotonic_ns()
            entered.put_nowait((threading.get_native_id(), started))
            failure = 0
            try:
                os.fdatasync(descriptor)
            except OSError as error:
                if error.errno is None:
                    raise
                failure = error.errno
            outcomes.put_nowait({"sync_errno": failure, "sync_elapsed_ns": time.monotonic_ns() - started})
        finally:
            os.close(descriptor)
            completed.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    tid, started = entered.get(timeout=5.0)
    sys.stdout.write(json.dumps({"pid": os.getpid(), "tid": tid, "sync_call_pending": True, "started_ns": started}) + "\n")
    sys.stdout.flush()
    deadline = started + 5_000_000_000
    finished = completed.wait(max(0.0, (deadline - time.monotonic_ns()) / 1_000_000_000))
    syscall_snapshot = None if finished else Path(f"/proc/self/task/{tid}/syscall").read_text().split()[0]
    sys.stdout.write(json.dumps({"native_fdatasync_observed": syscall_snapshot == str(native_sync_number), "syscall_snapshot": syscall_snapshot, "five_second_observation": "completed" if finished else "pending", "observed_ns": time.monotonic_ns(), "forced_exit_exercised": False}) + "\n")
    sys.stdout.flush()
    cleanup_deadline = deadline + 5_000_000_000
    if not completed.wait(max(0.0, (cleanup_deadline - time.monotonic_ns()) / 1_000_000_000)):
        raise TimeoutError("native_operation_unsettled")
    thread.join(timeout=max(0.0, (cleanup_deadline - time.monotonic_ns()) / 1_000_000_000))
    if thread.is_alive():
        raise RuntimeError("writer_not_closed")
    sys.stdout.write(json.dumps(outcomes.get_nowait()) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise ValueError("arguments")
    exercise(Path(sys.argv[1]), int(sys.argv[2]))
