import asyncio
import os
import threading
from pathlib import Path

import pytest

from kora.plugins.vcs.fake.source import DirInspectionSource, DirInspectionSources
from kora.protocols.errors import KoraProtocolError


@pytest.mark.parametrize(
    "ending", ["cancel", "repeated_cancel", "worker_error", "cancelled_worker_error"]
)
def test_inspection_context_owns_active_read_until_completion(
    tmp_path: Path, ending: str
) -> None:
    path = tmp_path / "content"
    path.write_bytes(b"data")
    release = threading.Event()
    closed = threading.Event()
    baseline = len(os.listdir("/dev/fd"))

    async def exercise() -> None:
        entered = asyncio.Event()
        finished = asyncio.Event()
        loop = asyncio.get_running_loop()
        source = DirInspectionSource(tmp_path)

        def operation() -> bytes:
            descriptor = os.open(path, os.O_RDONLY)
            try:
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(30):
                    raise TimeoutError
                if ending in ("worker_error", "cancelled_worker_error"):
                    raise OSError("read refused")
                return os.read(descriptor, 4)
            finally:
                os.close(descriptor)
                closed.set()

        async def owning_context() -> None:
            try:
                try:
                    await source.owner.run(operation)
                finally:
                    await source.owner.close()
            finally:
                finished.set()

        task = asyncio.create_task(owning_context())
        try:
            await asyncio.wait_for(entered.wait(), 30)
            with pytest.raises(KoraProtocolError):
                await source.index("content", 4)
            if ending != "worker_error":
                task.cancel()
                assert await asyncio.to_thread(source.owner.stopping.wait, 30)
                if ending == "repeated_cancel":
                    task.cancel()
                with pytest.raises(KoraProtocolError):
                    await source.index("content", 4)
            progressed = asyncio.Event()
            loop.call_soon(progressed.set)
            await asyncio.wait_for(progressed.wait(), 30)
            assert not finished.is_set()
            assert not closed.is_set()
        finally:
            release.set()
        if ending == "worker_error":
            with pytest.raises(OSError, match="read refused"):
                await asyncio.wait_for(task, 30)
        else:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 30)
        assert finished.is_set()
        assert source.owner.task is None

    asyncio.run(exercise())

    assert closed.is_set()
    assert len(os.listdir("/dev/fd")) == baseline


def test_inspection_source_normal_and_empty_close(tmp_path: Path) -> None:
    (tmp_path / "index").write_bytes(b"content")

    async def exercise() -> bytes:
        sources = DirInspectionSources()
        async with sources.open(str(tmp_path)) as source:
            found = await source.index("index", 7)
        async with sources.open(str(tmp_path)):
            pass
        with pytest.raises(KoraProtocolError):
            await source.index("index", 7)
        return found

    found = asyncio.run(exercise())

    assert found == b"content"


@pytest.mark.parametrize("selection", ["index", "blob"])
def test_inspection_filesystem_refusal_is_structured(
    tmp_path: Path, selection: str
) -> None:
    (tmp_path / "target").write_bytes(b"secret")
    blobs = tmp_path / "store" / "blobs"
    blobs.mkdir(parents=True)
    location = "a" * 64
    target = tmp_path / "index" if selection == "index" else blobs / location
    target.symlink_to(tmp_path / "target")

    async def exercise() -> None:
        async with DirInspectionSources().open(str(tmp_path)) as source:
            if selection == "index":
                await source.index("index", 100)
            else:
                async for _ in source.content(location, 100):
                    pass

    with pytest.raises(KoraProtocolError) as raised:
        asyncio.run(exercise())

    assert raised.value.code == "vcs_inspection_unavailable"


def test_cancelled_queued_read_never_opens_its_file(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    release = threading.Event()
    busy = threading.Event()
    created = tmp_path / "not-opened"

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        source = DirInspectionSource(tmp_path)
        entered = asyncio.Event()

        def occupying() -> None:
            busy.set()
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(30):
                raise TimeoutError

        def queued() -> None:
            created.write_bytes(b"unexpected")

        occupancy = loop.run_in_executor(None, occupying)
        await asyncio.wait_for(entered.wait(), 30)
        read = asyncio.create_task(source.owner.run(queued))
        progressed = asyncio.Event()
        loop.call_soon(progressed.set)
        await progressed.wait()
        try:
            assert source.owner.task is not None
            read.cancel()
            progressed.clear()
            loop.call_soon(progressed.set)
            await progressed.wait()
            assert source.owner.stopping.is_set()
            assert not read.done()
        finally:
            release.set()
        await asyncio.wait_for(occupancy, 30)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(read, 30)
        await source.owner.close()

    asyncio.run(exercise())

    assert not created.exists()
