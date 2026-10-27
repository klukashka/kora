import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from typing import BinaryIO

import pytest

from kora.protocols.time import SystemClock
from kora.runner.identity import IdentityState, NativeIdentityReader, open_status


class ReapingStatusOpener:
    def __init__(self, bystander: subprocess.Popen[bytes]) -> None:
        self.bystander = bystander

    def __call__(self, pid: int) -> BinaryIO:
        status = open_status(pid)
        if pid == self.bystander.pid and self.bystander.returncode is None:
            self.bystander.kill()
            self.bystander.wait()
        return status


class SteppingClock(SystemClock):
    def __init__(self) -> None:
        self.instant = 0

    def monotonic_ns(self) -> int:
        self.instant += 1
        return self.instant


def open_descriptors() -> int:
    return len(os.listdir("/proc/self/fd" if sys.platform == "linux" else "/dev/fd"))


def start_group_leader() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-I", "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        process_group=0,
    )


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.returncode is None:
        process.kill()
        process.wait()
    if process.stdin is not None:
        process.stdin.close()


@pytest.fixture
def observed() -> Iterator[subprocess.Popen[bytes]]:
    process = start_group_leader()
    yield process
    stop(process)


@pytest.fixture
def bystander() -> Iterator[subprocess.Popen[bytes]]:
    process = start_group_leader()
    yield process
    stop(process)


def test_native_group_scan_excludes_a_process_reaped_after_its_status_was_opened(
    observed: subprocess.Popen[bytes], bystander: subprocess.Popen[bytes]
) -> None:
    if sys.platform == "linux":
        reader = NativeIdentityReader(status_opener=ReapingStatusOpener(bystander))
        descriptors = open_descriptors()

        group = reader.group(observed.pid)

        assert bystander.returncode == -signal.SIGKILL
        assert group.state is IdentityState.PRESENT
        assert [pid for pid, _ in group.members] == [observed.pid]
        assert open_descriptors() == descriptors
    else:
        reader = NativeIdentityReader()
        listed = reader.group(observed.pid)
        observed.kill()
        observed.wait()

        observation = reader.read(observed.pid)

        assert [pid for pid, _ in listed.members] == [observed.pid]
        assert observation.state is IdentityState.ABSENT


@pytest.mark.parametrize(
    ("exit_path", "expected"),
    [
        ("completed", IdentityState.PRESENT),
        ("capacity", IdentityState.UNKNOWN),
        ("deadline", IdentityState.UNKNOWN),
    ],
)
def test_native_group_scan_releases_descriptors_on_every_exit(
    observed: subprocess.Popen[bytes], exit_path: str, expected: IdentityState
) -> None:
    clock = SteppingClock()
    reader = NativeIdentityReader(
        clock=clock, max_scan_entries=1 if exit_path == "capacity" else 65536
    )
    deadline = clock.instant + 3 if exit_path == "deadline" else None
    descriptors = open_descriptors()

    group = reader.group(observed.pid, deadline)

    assert group.state is expected
    assert open_descriptors() == descriptors
