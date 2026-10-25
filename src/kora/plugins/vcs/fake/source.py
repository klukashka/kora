import asyncio
import os
import re
import threading
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from kora.executor.failures import Phase, invalid
from kora.executor.files import DIRECTORY_FLAGS
from kora.executor.scan import InspectionSource, InspectionSources
from kora.plugins.vcs.fake import store
from kora.protocols.deadlines import NANOSECONDS_PER_SECOND
from kora.protocols.errors import KoraProtocolError

MIB: Final[int] = 1_048_576
NAME: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._-]{1,255}")
READ_FLAGS: Final[int] = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def unavailable() -> KoraProtocolError:
    return KoraProtocolError(
        "vcs_inspection_unavailable", "the inspection source is unavailable", True, {}
    )


def reachable_sync(
    work: Path, revision: str, base: str, cancelled: threading.Event
) -> frozenset[str]:
    root = os.open(work / store.PREPARED_STORE, DIRECTORY_FLAGS)
    prepared = store.Store(root)
    try:
        revisions = store.new_revisions(
            prepared, revision, base, store.MAX_PREPARED_REVISIONS, cancelled.is_set
        )
        named: set[str] = set()
        for item in revisions:
            named.update(entry.sha256 for entry in prepared.tree(item.tree))
        lineage = store.ancestry(prepared, base, cancelled=cancelled.is_set)
        held = store.base_sizes(prepared, lineage, named, cancelled.is_set)
    finally:
        prepared.close()
        os.close(root)
    return frozenset({item.identifier for item in revisions} | (named - held.keys()))


class DirInspectionSource(InspectionSource):
    def __init__(self, work: Path) -> None:
        self._work = work

    async def index(self, location: str, limit: int) -> bytes:
        if NAME.fullmatch(location) is None or location in (".", ".."):
            invalid("content_index_location", Phase.SCAN)
        directory = os.open(self._work, DIRECTORY_FLAGS)
        try:
            return await asyncio.to_thread(
                store.read_limited, directory, location, limit
            )
        except FileNotFoundError:
            invalid("content_index_missing", Phase.SCAN)
        except store.Refused:
            invalid("content_index_oversized", Phase.SCAN)
        finally:
            os.close(directory)

    def opened(self, location: str) -> int:
        if not store.is_identifier(location):
            invalid("content_location_invalid", Phase.SCAN)
        root = os.open(self._work / store.PREPARED_STORE, DIRECTORY_FLAGS)
        try:
            for section in ("blobs", "revisions"):
                directory = store.open_directory(root, section)
                try:
                    return os.open(location, READ_FLAGS, dir_fd=directory)
                except FileNotFoundError:
                    continue
                finally:
                    os.close(directory)
        finally:
            os.close(root)
        invalid("content_missing", Phase.SCAN)

    async def content(self, location: str, limit: int) -> AsyncIterator[bytes]:
        descriptor = self.opened(location)
        try:
            remaining = limit + 1
            while remaining:
                block = await asyncio.to_thread(
                    os.read, descriptor, min(MIB, remaining)
                )
                if not block:
                    return
                remaining -= len(block)
                yield block
        finally:
            os.close(descriptor)

    async def revision_bytes(self, revision: str, limit: int) -> bytes:
        if not store.is_identifier(revision):
            invalid("content_location_invalid", Phase.PREPARE)
        root = os.open(self._work / store.PREPARED_STORE, DIRECTORY_FLAGS)
        try:
            revisions = store.open_directory(root, "revisions")
            try:
                data = store.read_limited(revisions, revision, limit)
            finally:
                os.close(revisions)
        except (OSError, store.Refused):
            raise unavailable() from None
        finally:
            os.close(root)
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
