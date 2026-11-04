import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TypeVar

Result = TypeVar("Result")


class NativeIOBusy(Exception):
    pass


class NativeIOClosed(Exception):
    pass


class NativeIOUnresolved(Exception):
    pass


@dataclass(eq=False)
class NativeOperation:
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    retained: object | None = None


async def completed(future: Future[Result]) -> bool:
    observation = asyncio.wrap_future(future)
    cancelled = False
    while not observation.done():
        try:
            await asyncio.wait({observation})
        except asyncio.CancelledError:
            cancelled = True
    if not observation.cancelled() and observation.done():
        observation.exception()
    return cancelled


class NativeExecutor:
    def __init__(self, workers: int, admitted: int) -> None:
        self.executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="kora-runtime-local"
        )
        self.admitted = admitted
        self.jobs: set[NativeOperation] = set()
        self.closed = False
        self.unresolved = False
        self.closing: asyncio.Task[None] | None = None

    async def run(
        self,
        function: Callable[[], Result],
        on_cancel: Callable[[Result], None] | None = None,
    ) -> Result:
        if self.closed:
            raise NativeIOClosed
        if self.unresolved:
            raise NativeIOUnresolved
        if len(self.jobs) >= self.admitted:
            raise NativeIOBusy
        owned = NativeOperation()
        self.jobs.add(owned)
        unresolved = True

        def invoke() -> Result:
            value = function()
            owned.retained = value
            return value

        try:
            future = self.executor.submit(invoke)
            unresolved = False
            cancelled = await completed(future)
            if future.cancelled():
                unresolved = True
                raise NativeIOUnresolved
            try:
                result = future.result()
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
            if cancelled and on_cancel is not None:
                unresolved = True
                cleanup = self.executor.submit(on_cancel, result)
                await completed(cleanup)
                cleanup.result()
                unresolved = False
            if cancelled:
                raise asyncio.CancelledError
            return result
        finally:
            if unresolved:
                self.unresolved = True
            else:
                owned.retained = None
                self.jobs.discard(owned)
            owned.settled.set()

    async def drain(self) -> None:
        for job in tuple(self.jobs):
            while not job.settled.is_set():
                try:
                    await job.settled.wait()
                except asyncio.CancelledError:
                    continue
        joining = asyncio.get_running_loop().run_in_executor(
            None, self.executor.shutdown, True
        )
        while not joining.done():
            try:
                await asyncio.wait({joining})
            except asyncio.CancelledError:
                continue
        joining.result()
        if self.unresolved:
            raise NativeIOUnresolved

    async def close(self) -> None:
        self.closed = True
        cancelled = False
        while True:
            if self.closing is None or self.closing.cancelled():
                self.closing = asyncio.create_task(self.drain())
            if self.closing.done():
                self.closing.result()
                break
            try:
                await asyncio.shield(self.closing)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
