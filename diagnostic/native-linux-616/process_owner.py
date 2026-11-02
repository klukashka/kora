from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

STARTED: float = time.monotonic()


UNSETTLED_CHILDREN: list[subprocess.Popen[bytes]] = []
UNCONFIRMED_GROUPS: set[int] = set()


def command(argv: list[str], *, cleanup: bool = False, runner: tuple[int, int] | None = None, expected_status: int = 0) -> bytes:
    if UNCONFIRMED_GROUPS:
        raise RuntimeError("process_group_cleanup_unconfirmed")
    remaining = 120.0 - (time.monotonic() - STARTED)
    if remaining <= 0 and not cleanup:
        raise TimeoutError("probe_budget")
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, user=None if runner is None else runner[0], group=None if runner is None else runner[1], extra_groups=None if runner is None else [])
    try:
        try:
            output, errors = process.communicate(timeout=20.0 if cleanup else min(20.0, remaining))
        except BaseException:
            UNCONFIRMED_GROUPS.add(process.pid)
            UNSETTLED_CHILDREN.append(process)
            if process.returncode is not None:
                raise
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=5.0)
            except subprocess.TimeoutExpired:
                raise RuntimeError("child_cleanup_unconfirmed") from None
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                UNCONFIRMED_GROUPS.discard(process.pid)
            raise
        if expected_status != 0 and errors:
            raise RuntimeError("unexpected_failure_stderr")
        if process.returncode != expected_status:
            sys.stderr.write(json.dumps({"command": Path(argv[0]).name, "stdout": output[:4096].decode("utf-8", errors="replace"), "stderr": errors[:4096].decode("utf-8", errors="replace")}) + "\n")
            raise RuntimeError(f"command_failed:{Path(argv[0]).name}:{process.returncode}")
        return output
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


