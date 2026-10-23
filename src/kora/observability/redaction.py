import codecs
import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType

from kora.observability.settings import (
    MAX_SECRET_BYTES,
    MAX_SECRET_VALUE_BYTES,
    MAX_SECRETS,
    MIN_SECRET_LENGTH,
)

MASK: str = "[redacted]"
MAX_CHUNK_BYTES: int = 65_536


class SecretAdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class RegistryLimits:
    max_values: int = MAX_SECRETS
    max_bytes: int = MAX_SECRET_BYTES
    min_length: int = MIN_SECRET_LENGTH
    max_value_bytes: int = MAX_SECRET_VALUE_BYTES


DEFAULT_LIMITS: RegistryLimits = RegistryLimits()


@dataclass(frozen=True)
class Snapshot:
    values: tuple[str, ...]
    longest: int
    groups: tuple[tuple[str, tuple[str, ...]], ...]


EMPTY_SNAPSHOT: Snapshot = Snapshot((), 0, ())


def grouped(values: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    groups: dict[str, list[str]] = {}
    for value in values:
        groups.setdefault(value[0], []).append(value)
    return tuple((first, tuple(members)) for first, members in groups.items())


def snapshot_of(values: list[str]) -> Snapshot:
    if not values:
        return EMPTY_SNAPSHOT
    ordered = tuple(sorted(set(values), key=len, reverse=True))
    return Snapshot(ordered, len(ordered[0]), grouped(ordered))


def value_spans(text: str, value: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    position = text.find(value)
    while position != -1:
        end = position + len(value)
        spans.append((position, end))
        position = text.find(value, end)
    return spans


def candidates(text: str, snapshot: Snapshot) -> list[str]:
    present = set(text)
    return [
        value
        for first, members in snapshot.groups
        if first in present
        for value in members
    ]


def matched_spans(text: str, snapshot: Snapshot) -> list[tuple[int, int]]:
    found = sorted(
        span
        for value in candidates(text, snapshot)
        for span in value_spans(text, value)
    )
    merged: list[tuple[int, int]] = []
    for start, end in found:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def masked_until(
    text: str, snapshot: Snapshot, boundary: int, final: bool
) -> tuple[str, int]:
    parts: list[str] = []
    position = 0
    for start, end in matched_spans(text, snapshot):
        if not final and start >= boundary:
            break
        parts.append(text[position:start])
        parts.append(MASK)
        position = end
    emitted = len(text) if final else max(position, boundary)
    parts.append(text[position:emitted])
    return "".join(parts), emitted


def masked_prefix(snapshot: Snapshot, text: str, retained: int) -> str:
    window = retained + snapshot.longest
    tail = snapshot.longest - 1
    parts: list[str] = []
    produced = 0
    position = 0
    while position < len(text) and produced <= retained:
        end = min(len(text), position + window)
        chunk = text[position:end]
        final = end == len(text)
        masked, emitted = masked_until(chunk, snapshot, len(chunk) - tail, final)
        parts.append(masked)
        produced += len(masked)
        position += emitted
    return "".join(parts)


def held_suffix(text: str, values: tuple[str, ...]) -> int:
    held = 0
    for value in values:
        limit = min(len(value) - 1, len(text))
        if limit <= held:
            continue
        position = text.find(value[0], len(text) - limit)
        while position != -1:
            if value.startswith(text[position:]):
                held = len(text) - position
                break
            position = text.find(value[0], position + 1)
    return held


class SecretRegistration:
    def __init__(self, registry: "SecretRegistry", handle: int) -> None:
        self._registry = registry
        self._handle = handle

    def release(self) -> None:
        self._registry.release(self._handle)


class SecretRegistry:
    def __init__(self, limits: RegistryLimits = DEFAULT_LIMITS) -> None:
        self._limits = limits
        self._lock = threading.Lock()
        self._values: dict[int, str] = {}
        self._next_handle = 1
        self._retained_bytes = 0
        self._snapshot = EMPTY_SNAPSHOT

    def register(self, value: str) -> SecretRegistration:
        size = len(value.encode("utf-8"))
        if size < self._limits.min_length:
            raise SecretAdmissionError("a secret shorter than the minimum is refused")
        if size > self._limits.max_value_bytes:
            raise SecretAdmissionError(
                "a secret longer than the value bound is refused"
            )
        with self._lock:
            if len(self._values) >= self._limits.max_values:
                raise SecretAdmissionError("the secret registry is full")
            if self._retained_bytes + size > self._limits.max_bytes:
                raise SecretAdmissionError("the secret registry byte bound is reached")
            handle = self._next_handle
            self._next_handle += 1
            self._values[handle] = value
            self._retained_bytes += size
            self._snapshot = snapshot_of(list(self._values.values()))
        return SecretRegistration(self, handle)

    def release(self, handle: int) -> None:
        with self._lock:
            value = self._values.pop(handle, None)
            if value is None:
                return
            self._retained_bytes -= len(value.encode("utf-8"))
            self._snapshot = snapshot_of(list(self._values.values()))

    def snapshot(self) -> Snapshot:
        return self._snapshot

    def mask(self, text: str, retained: int | None = None) -> str:
        snapshot = self._snapshot
        if not snapshot.values:
            return text
        if retained is None or len(text) <= retained + snapshot.longest:
            return masked_until(text, snapshot, len(text), True)[0]
        return masked_prefix(snapshot, text, retained)

    def count(self) -> int:
        with self._lock:
            return len(self._values)

    def retained_bytes(self) -> int:
        with self._lock:
            return self._retained_bytes


class StreamMasker:
    def __init__(self, registry: SecretRegistry, emit: Callable[[str], None]) -> None:
        self._registry = registry
        self._emit = emit
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._held = ""
        self._closed = False

    def feed(self, data: bytes) -> None:
        if self._closed:
            raise ValueError("the stream masker is closed")
        for start in range(0, len(data), MAX_CHUNK_BYTES):
            self._mask_chunk(
                self._decoder.decode(data[start : start + MAX_CHUNK_BYTES])
            )

    def _mask_chunk(self, text: str) -> None:
        buffer = self._held + text
        snapshot = self._registry.snapshot()
        boundary = len(buffer) - held_suffix(buffer, snapshot.values)
        masked, emitted = masked_until(buffer, snapshot, boundary, False)
        self._held = buffer[emitted:]
        if masked:
            self._emit(masked)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        buffer = self._held + self._decoder.decode(b"", final=True)
        self._held = ""
        masked = masked_until(buffer, self._registry.snapshot(), 0, True)[0]
        if masked:
            self._emit(masked)

    @property
    def held(self) -> int:
        return len(self._held)

    def __enter__(self) -> "StreamMasker":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()
