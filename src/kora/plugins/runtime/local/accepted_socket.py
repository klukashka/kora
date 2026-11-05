import asyncio
import socket
from collections.abc import Callable, Coroutine

from kora.runner.channels import DescriptorReadiness


class AcceptedSocket:
    def __init__(self, endpoint: socket.socket, stamp: int) -> None:
        self.endpoint = endpoint
        self.stamp = stamp
        self._started = False

    def close(self) -> None:
        self.endpoint.close()

    def start(
        self,
        handler: Callable[[socket.socket, int], Coroutine[object, object, None]],
    ) -> asyncio.Task[None]:
        if self._started:
            raise RuntimeError("accepted socket already handed off")
        coroutine: Coroutine[object, object, None] | None = None
        try:
            coroutine = handler(self.endpoint, self.stamp)
            task = asyncio.create_task(coroutine)
        except BaseException:
            if coroutine is not None:
                coroutine.close()
            self.close()
            raise
        self._started = True
        task.add_done_callback(self._settled)
        return task

    def _settled(self, task: asyncio.Task[None]) -> None:
        self.close()


async def accept_socket(
    listener: socket.socket, stamp: Callable[[], int]
) -> AcceptedSocket:
    while True:
        ready = DescriptorReadiness(listener.fileno(), writing=False)
        try:
            await ready.ready
        finally:
            ready.close()
        try:
            endpoint, _ = listener.accept()
        except BlockingIOError:
            continue
        try:
            return AcceptedSocket(endpoint, stamp())
        except BaseException:
            endpoint.close()
            raise
