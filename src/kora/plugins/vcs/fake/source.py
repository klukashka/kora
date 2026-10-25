import asyncio
import os
import re
import threading
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from kora.executor.failures import Phase, invalid
from kora.executor.scan import InspectionSource, InspectionSources
from kora.plugins.vcs.fake import store
from kora.protocols.deadlines import NANOSECONDS_PER_SECOND
from kora.protocols.errors import KoraProtocolError

NAME: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._-]{1,255}")
CONTENT_SECTIONS: Final[tuple[str, ...]] = ("blobs", "revisions")


def unavailable() -> KoraProtocolError:
    return KoraProtocolError(
        "vcs_inspection_unavailable", "the inspection source is unavailable", True, {}
    )


def read_in(directory: Path, name: str, limit: int) -> bytes:
    descriptor = store.open_directory(None, str(directory))
    try:
        return store.read_limited(descriptor, name, limit)
    finally:
        os.close(descriptor)


def opened_content(work: Path, location: str) -> int | None:
    root = store.open_directory(None, str(work / store.PREPARED_STORE))
    try:
        for section in CONTENT_SECTIONS:
            directory = store.open_directory(root, section)
            try:
                return store.open_regular(directory, location)
            except FileNotFoundError:
                continue
            finally:
                os.close(directory)
    finally:
        os.close(root)
    return None


def reachable_sync(
    work: Path, revision: str, base: str, cancelled: threading.Event
) -> frozenset[str]:
    root = store.open_directory(None, str(work / store.PREPARED_STORE))
    try:
        prepared = store.Store(root)
        try:
            revisions, fresh = store.new_objects(
                prepared, base, revision, cancelled.is_set
            )
        finally:
            prepared.close()
    finally:
        os.close(root)
    return frozenset({item.identifier for item in revisions} | fresh)


def chunk_at(work: Path, location: str, offset: int, size: int) -> bytes | None:
    descriptor = opened_content(work, location)
    if descriptor is None:
        return None
    try:
        return os.pread(descriptor, size, offset)
    finally:
        os.close(descriptor)


class DirInspectionSource(InspectionSource):
    def __init__(self, work: Path) -> None:
        self._work = work

    async def index(self, location: str, limit: int) -> bytes:
        if NAME.fullmatch(location) is None or location in (".", ".."):
            invalid("content_index_location", Phase.SCAN)
        try:
            return await asyncio.to_thread(read_in, self._work, location, limit)
        except FileNotFoundError:
            invalid("content_index_missing", Phase.SCAN)
        except store.Refused:
            invalid("content_index_oversized", Phase.SCAN)

    async def content(self, location: str, limit: int) -> AsyncIterator[bytes]:
        if not store.is_identifier(location):
            invalid("content_location_invalid", Phase.SCAN)
        offset = 0
        while offset <= limit:
            size = min(store.MIB, limit + 1 - offset)
            block = await asyncio.to_thread(
                chunk_at, self._work, location, offset, size
            )
            if block is None:
                invalid("content_missing", Phase.SCAN)
            if not block:
                return
            offset += len(block)
            yield block

    async def revision_bytes(self, revision: str, limit: int) -> bytes:
        if not store.is_identifier(revision):
            invalid("content_location_invalid", Phase.PREPARE)
        directory = self._work / store.PREPARED_STORE / "revisions"
        try:
            data = await asyncio.to_thread(read_in, directory, revision, limit)
        except (OSError, store.Refused):
            raise unavailable() from None
        if store.digest_of(data) != revision:
            invalid("revision_digest_mismatch", Phase.PREPARE)
        return data

    async def reachable(
        self, revision: str, base: str, timeout_ns: int
    ) -> frozenset[str]:
        if not (store.is_identifier(revision) and store.is_identifier(base)):
            invalid("content_location_invalid", Phase.SCAN)
        cancelled = threading.Event()
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    reachable_sync, self._work, revision, base, cancelled
                ),
                timeout_ns / NANOSECONDS_PER_SECOND,
            )
        except store.Refused:
            invalid("content_unreachable", Phase.SCAN)
        except (TimeoutError, OSError, store.Failed):
            raise unavailable() from None
        finally:
            cancelled.set()


class DirInspectionSources(InspectionSources):
    @asynccontextmanager
    async def open(self, prepared_location: str) -> AsyncGenerator[InspectionSource]:
        yield DirInspectionSource(Path(prepared_location))
