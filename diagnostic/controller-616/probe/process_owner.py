from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

STARTED: float = time.monotonic()
ACTIVE_DEADLINE: ContextVar[float | None] = ContextVar(
    "probe_command_deadline", default=None
)
UNSETTLED_CHILDREN: list[subprocess.Popen[bytes]] = []
UNCONFIRMED_GROUPS: set[int] = set()


@contextmanager
def command_deadline(deadline: float) -> Generator[None]:
    previous = ACTIVE_DEADLINE.get()
    token = ACTIVE_DEADLINE.set(
        deadline if previous is None else min(previous, deadline)
    )
    try:
        yield
    finally:
        ACTIVE_DEADLINE.reset(token)


def group_absent(process: subprocess.Popen[bytes]) -> bool:
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return True
    return False


def settle(process: subprocess.Popen[bytes]) -> None:
    UNCONFIRMED_GROUPS.add(process.pid)
    if process not in UNSETTLED_CHILDREN:
        UNSETTLED_CHILDREN.append(process)
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5.0)
    if not group_absent(process):
        raise RuntimeError("process_group_cleanup_unconfirmed")
    UNCONFIRMED_GROUPS.remove(process.pid)


def command(
    argv: list[str],
    *,
    cleanup: bool = False,
    runner: tuple[int, int] | None = None,
    expected_status: int = 0,
) -> bytes:
    if UNCONFIRMED_GROUPS:
        raise RuntimeError("process_group_cleanup_unconfirmed")
    now = time.monotonic()
    deadline = now + 20.0
    if not cleanup:
        deadline = min(deadline, STARTED + 120.0)
    active = ACTIVE_DEADLINE.get()
    if active is not None:
        deadline = min(deadline, active)
    if deadline <= now:
        raise TimeoutError("probe_budget")
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        user=None if runner is None else runner[0],
        group=None if runner is None else runner[1],
        extra_groups=None if runner is None else [],
    )
    try:
        output, errors = process.communicate(
            timeout=max(0.0, deadline - time.monotonic())
        )
        if expected_status != 0 and errors:
            raise RuntimeError("unexpected_failure_stderr")
        if process.returncode != expected_status:
            sys.stderr.write(
                json.dumps(
                    {
                        "command": Path(argv[0]).name,
                        "stdout": output[:4096].decode("utf-8", errors="replace"),
                        "stderr": errors[:4096].decode("utf-8", errors="replace"),
                    }
                )
                + "\n"
            )
            raise RuntimeError(
                f"command_failed:{Path(argv[0]).name}:{process.returncode}"
            )
        return output
    finally:
        try:
            settle(process)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
