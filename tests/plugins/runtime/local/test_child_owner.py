import asyncio
import os
import signal
import sys
import threading
from pathlib import Path

import pytest

from kora.plugins.runtime.local.child_owner import ChildOwner
from kora.plugins.runtime.local.spawn import (
    Launcher,
    Launchers,
    Readiness,
    spawn_runner,
)
from kora.protocols.time import SystemClock
from tests.support.local_runtime import open_descriptors

pytestmark = pytest.mark.asyncio


def gated_child(owner: ChildOwner) -> tuple[int, int]:
    reading, writing = os.pipe()
    try:
        pid = owner.start(
            lambda: os.posix_spawn(
                sys.executable,
                (sys.executable, "-I", "-c", "import os; os.read(0, 1)"),
                {},
                file_actions=[
                    (os.POSIX_SPAWN_DUP2, reading, 0),
                    (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
                    (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0),
                ],
                setsigmask=(),
                setsigdef=(signal.SIGTERM,),
            )
        )
        return pid, writing
    except BaseException:
        os.close(writing)
        raise
    finally:
        os.close(reading)


async def release_child(owner: ChildOwner) -> None:
    owner.abort()
    owner.signal(signal.SIGKILL)
    assert await asyncio.to_thread(owner.finished.wait, 5)
    await owner.join()


async def test_launcher_spawn_failure_releases_prepared_owner(tmp_path: Path) -> None:
    owner = ChildOwner()
    descriptors = open_descriptors()

    try:
        with pytest.raises(FileNotFoundError):
            owner.start(
                lambda: os.posix_spawn(str(tmp_path / "absent"), ("absent",), {})
            )
        assert await asyncio.to_thread(owner.finished.wait, 5)
        await owner.join()
    finally:
        await release_child(owner)

    assert owner.pid is None
    assert owner.status is None
    assert not owner.thread.is_alive()
    assert open_descriptors() == descriptors


async def test_launcher_is_owned_before_readiness(tmp_path: Path) -> None:
    launchers = Launchers()
    clock = SystemClock()
    descriptors = open_descriptors()
    reapers = {
        thread for thread in threading.enumerate() if thread.name == "kora-child-reaper"
    }

    try:
        outcome = await spawn_runner(
            (str(tmp_path / "absent-runner"),), {}, clock, launchers
        )
    finally:
        await launchers.drain(clock)

    assert outcome is Readiness.ENDED
    assert launchers.spawning == launchers.settling == launchers.unsettled == set()
    assert open_descriptors() == descriptors
    assert {
        thread for thread in threading.enumerate() if thread.name == "kora-child-reaper"
    } == reapers


async def test_launcher_cancellation_keeps_reap_owned() -> None:
    launchers = Launchers()
    clock = SystemClock()
    launcher = Launcher()
    pid, writing = gated_child(launcher.child)
    entered = asyncio.Event()

    async def settle() -> None:
        entered.set()
        await launchers.settled(launcher, clock, kill=True)

    settling = asyncio.create_task(settle())
    try:
        await entered.wait()
        settling.cancel()
        result = await asyncio.gather(settling, return_exceptions=True)
        await launchers.drain(clock)
    finally:
        os.close(writing)
        await release_child(launcher.child)
        await launcher.close()

    assert isinstance(result[0], asyncio.CancelledError)
    assert launcher.reaped
    assert launcher.closed
    assert not launcher.child.thread.is_alive()
    assert launchers.settling == launchers.unsettled == set()
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


async def test_child_observation_survives_runtime_and_loop_close() -> None:
    async def prepare() -> tuple[ChildOwner, int, int]:
        owner = ChildOwner()
        pid, writing = gated_child(owner)
        return owner, pid, writing

    owner, pid, writing = await asyncio.to_thread(asyncio.run, prepare())
    try:
        assert owner.loop.is_closed()
        assert owner.thread.is_alive()
        assert not owner.finished.is_set()
        os.kill(pid, 0)
        os.write(writing, b"R")
        assert await asyncio.to_thread(owner.finished.wait, 5)
        await owner.join()
    finally:
        os.close(writing)
        await release_child(owner)

    assert owner.collected()
    assert owner.status == 0
    assert not owner.thread.is_alive()
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


async def test_child_owner_has_one_waiter() -> None:
    owner = ChildOwner()
    pid, writing = gated_child(owner)

    try:
        with pytest.raises(RuntimeError, match="child already bound"):
            owner.start(lambda: os.posix_spawn(sys.executable, (sys.executable,), {}))
        os.write(writing, b"R")
        await asyncio.wait_for(owner.exited.wait(), 5)
        await owner.join()
    finally:
        os.close(writing)
        await release_child(owner)

    assert owner.status == 0
    assert owner.collected()
    assert not owner.thread.is_alive()
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


@pytest.mark.parametrize("after_reap", [False, True])
async def test_child_signal_and_reap_are_serialized(after_reap: bool) -> None:
    owner = ChildOwner()
    pid, writing = gated_child(owner)

    try:
        if after_reap:
            os.write(writing, b"R")
            await asyncio.wait_for(owner.exited.wait(), 5)
        sent = await asyncio.to_thread(owner.signal, signal.SIGTERM)
        await asyncio.wait_for(owner.exited.wait(), 5)
        await owner.join()
        repeated = owner.signal(signal.SIGKILL)
    finally:
        os.close(writing)
        await release_child(owner)

    assert sent is not after_reap
    assert not repeated
    assert owner.status is not None
    assert os.waitstatus_to_exitcode(owner.status) == (
        0 if after_reap else -signal.SIGTERM
    )
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


class ConcurrentSettlementClock(SystemClock):
    def __init__(self) -> None:
        self.waiters = 0
        self.both_waiting = asyncio.Event()

    async def sleep_until(self, deadline_ns: int) -> None:
        self.waiters += 1
        if self.waiters == 2:
            self.both_waiting.set()
        await super().sleep_until(deadline_ns)


async def test_concurrent_launcher_settlement_closes_watch_once() -> None:
    clock = ConcurrentSettlementClock()
    launchers = Launchers()
    launcher = Launcher()
    descriptors = open_descriptors()
    pid, writing = gated_child(launcher.child)
    first = asyncio.create_task(launchers.settled(launcher, clock, kill=False))
    second = asyncio.create_task(launchers.settled(launcher, clock, kill=False))

    try:
        await asyncio.wait_for(clock.both_waiting.wait(), 5)
        os.write(writing, b"R")
        await asyncio.gather(first, second)
        await launchers.drain(clock)
    finally:
        os.close(writing)
        await release_child(launcher.child)
        await asyncio.gather(first, second, return_exceptions=True)
        await launcher.close()

    assert launcher.closed
    assert launcher.reaped
    assert launchers.settling == launchers.unsettled == set()
    assert not launcher.child.thread.is_alive()
    assert open_descriptors() == descriptors
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)
