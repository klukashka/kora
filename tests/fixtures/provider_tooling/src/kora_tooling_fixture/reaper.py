import asyncio
from dataclasses import dataclass, field

from kora.conformance.io_helper import KernelChildReaper


def notify(changed: asyncio.Future[None]) -> None:
    if not changed.done():
        changed.set_result(None)


@dataclass
class HeldExit:
    returncode: int
    refusals: int | None
    watches: list[asyncio.Future[None]] = field(
        default_factory=list[asyncio.Future[None]]
    )


class DeferredReaper:
    def __init__(self, refusals: int = 2) -> None:
        self.kernel = KernelChildReaper()
        self.refusals = refusals
        self.held: dict[int, HeldExit] = {}
        self.deferred: set[int] = set()
        self.holding: set[int] = set()

    def hold(self, pid: int) -> None:
        self.holding.add(pid)

    def reap(self, pid: int) -> int | None:
        held = self.held.get(pid)
        if held is None:
            returncode = self.kernel.reap(pid)
            if returncode is None:
                return None
            held = HeldExit(returncode, None if pid in self.holding else self.refusals)
            self.held[pid] = held
            self.deferred.add(pid)
        if held.refusals == 0:
            del self.held[pid]
            return held.returncode
        if held.refusals is not None:
            held.refusals -= 1
            if held.refusals == 0:
                self.release_watches(held)
        return None

    def complete(self, pid: int) -> None:
        held = self.held[pid]
        held.refusals = 0
        self.release_watches(held)

    def release_watches(self, held: HeldExit) -> None:
        for changed in held.watches:
            notify(changed)
        held.watches.clear()

    def watch(self, pid: int) -> asyncio.Future[None]:
        changed: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        held = self.held.get(pid)
        if held is not None:
            held.watches.append(changed)
            changed.add_done_callback(lambda _: self.forget(pid, changed))
            return changed
        kernel = self.kernel.watch(pid)
        kernel.add_done_callback(lambda _: notify(changed))
        changed.add_done_callback(lambda _: kernel.cancel())
        return changed

    def forget(self, pid: int, changed: asyncio.Future[None]) -> None:
        held = self.held.get(pid)
        if held is not None and changed in held.watches:
            held.watches.remove(changed)
