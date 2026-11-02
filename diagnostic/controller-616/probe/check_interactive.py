from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from interactive import collect
from process_owner import (
    UNCONFIRMED_GROUPS,
    UNSETTLED_CHILDREN,
    command,
    command_deadline,
)


def exercise(mode: str) -> None:
    initial = len(os.listdir("/dev/fd"))
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("protocol_child.py")), mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    captured: list[tuple[bytes, bytes]] = []

    def activate(pid: int) -> None:
        if pid != process.pid:
            raise RuntimeError("unexpected_child")
        if mode == "activation_failure":
            raise RuntimeError("activation_failed")

    def observe(pid: int) -> None:
        if pid != process.pid:
            raise RuntimeError("unexpected_observed_child")

    def preserve(output: bytes, errors: bytes) -> None:
        captured.append((output, errors))

    failure: Exception | None = None
    try:
        collect(
            process,
            time.monotonic() + (0.2 if mode == "stall" else 5.0),
            activate,
            observe,
            preserve,
        )
    except (RuntimeError, TimeoutError) as error:
        failure = error
    if mode == "success":
        if failure is not None:
            raise failure
    elif failure is None:
        raise RuntimeError("invalid_protocol_accepted")
    if (
        process.returncode is None
        or UNCONFIRMED_GROUPS
        or any(child.returncode is None for child in UNSETTLED_CHILDREN)
    ):
        raise RuntimeError("child_not_settled")
    if len(captured) != 1 or len(os.listdir("/dev/fd")) != initial:
        raise RuntimeError("parent_resource_leak")


def deadline_control() -> None:
    try:
        with command_deadline(time.monotonic() + 0.2):
            command(
                [sys.executable, "-c", "import os; r,w=os.pipe(); os.read(r,1)"],
                cleanup=True,
            )
    except subprocess.TimeoutExpired:
        if UNCONFIRMED_GROUPS:
            raise RuntimeError("deadline_cleanup_unconfirmed") from None
    else:
        raise RuntimeError("cleanup_command_ignored_scoped_deadline")


def main() -> None:
    for mode in ("success", "foreign", "activation_failure", "overflow", "stall"):
        exercise(mode)
    deadline_control()
    sys.stdout.write(
        "6 nonprivileged ownership/protocol controls passed; "
        "no native fault exercised\n"
    )


if __name__ == "__main__":
    main()
