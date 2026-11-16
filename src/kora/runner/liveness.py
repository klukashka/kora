import asyncio
from typing import Final

from kora.protocols.deadlines import NANOSECONDS_PER_SECOND, LocalThresholds
from kora.protocols.time import Clock
from kora.runner.sink import RunnerSink

WRITER_STALL_NS: Final[int] = 30 * NANOSECONDS_PER_SECOND


async def watch_liveness(
    sink: RunnerSink,
    clock: Clock,
    stall_after_ns: int,
    deadline: LocalThresholds | None,
) -> None:
    while sink.cause() is None:
        now = clock.monotonic_ns()
        snapshot = sink.liveness()
        due: list[int] = []
        if snapshot.full_since_ns is not None:
            writer_due = snapshot.full_since_ns + WRITER_STALL_NS
            if writer_due <= now:
                sink.terminate("journal_unavailable")
                return
            due.append(writer_due)
        if deadline is not None:
            if deadline.interrupt_at_ns <= now:
                sink.terminate("deadline")
                return
            due.append(deadline.interrupt_at_ns)
        if not snapshot.calls_open:
            stall_due = snapshot.last_event_ns + stall_after_ns
            if stall_due <= now:
                sink.terminate("stall")
                return
            due.append(stall_due)
        changed = asyncio.create_task(sink.changed())
        stopped = asyncio.create_task(sink.termination())
        timer = asyncio.create_task(clock.sleep_until(min(due))) if due else None
        tasks = {changed, stopped} if timer is None else {changed, stopped, timer}
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.wait(tasks)
            for task in tasks:
                if not task.cancelled():
                    task.result()
