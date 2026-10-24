import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Final, Protocol

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    PlainSerializer,
    StrictBool,
    StrictInt,
    StringConstraints,
)
from pydantic_core import PydanticCustomError

from kora.ir.json import JsonObjectField
from kora.protocols.common import BoundaryModel

Identifier = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[\x21-\x7e]{1,256}$")
]
MAX_PID: Final[int] = 2**31 - 1
MAX_COUNT: Final[int] = 2**63 - 1
Pid = Annotated[StrictInt, Field(ge=1, le=MAX_PID)]
Count = Annotated[StrictInt, Field(ge=0, le=MAX_COUNT)]
Sha256 = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


def instant_refused() -> PydanticCustomError:
    return PydanticCustomError("instant_invalid", "the timestamp is invalid")


def instant_input(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            raise instant_refused() from None
    raise instant_refused()


def utc_instant(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise instant_refused()
    try:
        return value.astimezone(UTC)
    except OverflowError:
        raise instant_refused() from None


def instant_wire(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


Instant = Annotated[
    datetime,
    BeforeValidator(instant_input),
    AfterValidator(utc_instant),
    PlainSerializer(instant_wire, return_type=str, when_used="json"),
]


class ProcessIdentity(BoundaryModel):
    host_id: Identifier | None
    boot_id: Identifier | None
    pid_namespace: Identifier | None
    pid: Pid
    start_time: Count
    leads_group: StrictBool

    def identity_key(self) -> tuple[str | None, str | None, str | None, int, int]:
        return (
            self.host_id,
            self.boot_id,
            self.pid_namespace,
            self.pid,
            self.start_time,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProcessIdentity):
            return NotImplemented
        return self.identity_key() == other.identity_key()

    def __hash__(self) -> int:
        return hash(self.identity_key())


class ProcessIdentitySource(Protocol):
    def current(self) -> ProcessIdentity: ...


class GrantBinding(BoundaryModel):
    dispatcher_id: Identifier
    worker_id: Identifier
    incarnation_id: Identifier
    lease_epoch: Count
    execution_domain_id: Identifier
    process: ProcessIdentity | None


class Handle(BoundaryModel):
    runtime: Identifier
    attempt_id: Identifier
    execution_domain_id: Identifier
    host_hint: Identifier | None
    data: JsonObjectField


NULL_SIZE: Final[int] = 4
INSTANT_SIZE: Final[int] = 29


def text_size(value: str | None) -> int:
    if value is None:
        return NULL_SIZE
    if value.isascii() and value.isprintable():
        return len(value) + 2 + value.count('"') + value.count("\\")
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def integer_size(value: int | None) -> int:
    return NULL_SIZE if value is None else len(str(value))


def boolean_size(value: bool) -> int:
    return 4 if value else 5


def object_size(members: Mapping[str, int]) -> int:
    return (
        2
        + max(0, len(members) - 1)
        + sum(len(name) + 3 + size for name, size in members.items())
    )


def process_size(process: ProcessIdentity | None) -> int:
    if process is None:
        return NULL_SIZE
    return object_size(
        {
            "host_id": text_size(process.host_id),
            "boot_id": text_size(process.boot_id),
            "pid_namespace": text_size(process.pid_namespace),
            "pid": integer_size(process.pid),
            "start_time": integer_size(process.start_time),
            "leads_group": boolean_size(process.leads_group),
        }
    )


def grant_size(grant: GrantBinding) -> int:
    return object_size(
        {
            "dispatcher_id": text_size(grant.dispatcher_id),
            "worker_id": text_size(grant.worker_id),
            "incarnation_id": text_size(grant.incarnation_id),
            "lease_epoch": integer_size(grant.lease_epoch),
            "execution_domain_id": text_size(grant.execution_domain_id),
            "process": process_size(grant.process),
        }
    )


def handle_metadata_size(handle: Handle | None) -> int:
    if handle is None:
        return NULL_SIZE
    return object_size(
        {
            "runtime": text_size(handle.runtime),
            "attempt_id": text_size(handle.attempt_id),
            "execution_domain_id": text_size(handle.execution_domain_id),
            "host_hint": text_size(handle.host_hint),
            "data": NULL_SIZE,
        }
    )
