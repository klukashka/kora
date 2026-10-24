import hashlib
from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import Annotated, Final, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StrictBool,
    Tag,
    TypeAdapter,
    model_validator,
)
from pydantic_core import PydanticCustomError

from kora.ir.json import parse_json_object
from kora.ir.vcs_ref_json import canonical_bytes
from kora.protocols.common import BoundaryModel
from kora.protocols.evidence import (
    MAX_ENVELOPE_BYTES,
    DispatcherClosure,
    NormalCompletionQuiescence,
    PublicationOutcome,
    TerminationReceipt,
    normal_completion_size,
    outcome_metadata_size,
    termination_size,
)
from kora.protocols.identity import (
    INSTANT_SIZE,
    NULL_SIZE,
    Count,
    GrantBinding,
    Handle,
    Identifier,
    Instant,
    Sha256,
    boolean_size,
    handle_metadata_size,
    integer_size,
    object_size,
    text_size,
)

GRANT_HISTORY_LABEL: Final[str] = "kora.grant-history.v1"
CLOSURE_LABEL: Final[str] = "kora.dispatcher-closure.v1"
MAX_PAGE_ROWS: Final[int] = 128
MAX_OBSERVATION_METADATA_BYTES: Final[int] = 65_536
DIGEST_SIZE: Final[int] = 66


def history_refused() -> PydanticCustomError:
    return PydanticCustomError("history_invalid", "the history is invalid")


class HistoryEnvelopeExceeded(ValueError):
    def __init__(self) -> None:
        super().__init__("the history message exceeds its envelope")


def record_bytes(record: BaseModel) -> bytes:
    return canonical_bytes(parse_json_object(record.model_dump(mode="json")))


def chain_seed(
    label: str, namespace_id: str, attempt_id: str, execution_domain_id: str
) -> str:
    scope = canonical_bytes(
        {
            "namespace_id": namespace_id,
            "attempt_id": attempt_id,
            "execution_domain_id": execution_domain_id,
        }
    )
    return hashlib.sha256(label.encode("utf-8") + b"\x00" + scope).hexdigest()


def chain_step(previous: str, record: bytes) -> str:
    return hashlib.sha256(bytes.fromhex(previous) + record).hexdigest()


def extend(prefix_digest: str, records: Iterable[bytes]) -> str:
    digest = prefix_digest
    for record in records:
        digest = chain_step(digest, record)
    return digest


def item_digest(record: bytes) -> str:
    return hashlib.sha256(record).hexdigest()


class GrantHistorySnapshot(BoundaryModel):
    version: Literal[1]
    namespace_id: Identifier
    attempt_id: Identifier
    execution_domain_id: Identifier
    upper_epoch: Count | None
    count: Count
    digest: Sha256

    @model_validator(mode="after")
    def empty_history_is_seeded(self) -> Self:
        if (self.count == 0) != (self.upper_epoch is None):
            raise history_refused()
        if self.count == 0 and self.digest != self.seed():
            raise history_refused()
        return self

    def seed(self) -> str:
        return chain_seed(
            GRANT_HISTORY_LABEL,
            self.namespace_id,
            self.attempt_id,
            self.execution_domain_id,
        )

    def tip(self) -> "GrantHistoryPosition":
        return GrantHistoryPosition(
            after_epoch=self.upper_epoch,
            received_count=self.count,
            prefix_digest=self.digest,
        )

    def holds(self, position: "GrantHistoryPosition") -> bool:
        if position.received_count > self.count:
            return False
        if position.after_epoch is None:
            return position.prefix_digest == self.seed()
        return self.upper_epoch is not None and position.after_epoch <= self.upper_epoch


class GrantHistoryPosition(BoundaryModel):
    after_epoch: Count | None
    received_count: Count
    prefix_digest: Sha256

    @model_validator(mode="after")
    def empty_position_has_no_epoch(self) -> Self:
        if (self.received_count == 0) != (self.after_epoch is None):
            raise history_refused()
        return self


def grant_snapshot_size(snapshot: GrantHistorySnapshot) -> int:
    return object_size(
        {
            "version": 1,
            "namespace_id": text_size(snapshot.namespace_id),
            "attempt_id": text_size(snapshot.attempt_id),
            "execution_domain_id": text_size(snapshot.execution_domain_id),
            "upper_epoch": integer_size(snapshot.upper_epoch),
            "count": integer_size(snapshot.count),
            "digest": DIGEST_SIZE,
        }
    )


def grant_position_size(position: GrantHistoryPosition) -> int:
    return object_size(
        {
            "after_epoch": integer_size(position.after_epoch),
            "received_count": integer_size(position.received_count),
            "prefix_digest": DIGEST_SIZE,
        }
    )


def array_size(item_sizes: Iterable[int]) -> int:
    sizes = list(item_sizes)
    return 2 + max(0, len(sizes) - 1) + sum(sizes)


class GrantHistoryPage(BoundaryModel):
    snapshot: GrantHistorySnapshot
    start: GrantHistoryPosition
    grants: Annotated[tuple[GrantBinding, ...], Field(max_length=MAX_PAGE_ROWS)]
    end: GrantHistoryPosition
    complete: StrictBool

    @model_validator(mode="after")
    def page_extends_its_start(self) -> Self:
        snapshot = self.snapshot
        if not snapshot.holds(self.start) or not snapshot.holds(self.end):
            raise history_refused()
        epochs = [grant.lease_epoch for grant in self.grants]
        floor = self.start.after_epoch
        if floor is not None and epochs and epochs[0] <= floor:
            raise history_refused()
        if any(later <= earlier for earlier, later in pairwise(epochs)):
            raise history_refused()
        if any(
            grant.execution_domain_id != snapshot.execution_domain_id
            for grant in self.grants
        ):
            raise history_refused()
        records = [record_bytes(grant) for grant in self.grants]
        expected = GrantHistoryPosition(
            after_epoch=epochs[-1] if epochs else floor,
            received_count=self.start.received_count + len(records),
            prefix_digest=extend(self.start.prefix_digest, records),
        )
        if self.end != expected:
            raise history_refused()
        if self.complete != (self.end == snapshot.tip()):
            raise history_refused()
        if not self.grants and not self.complete:
            raise history_refused()
        if grant_page_size(self, records) > MAX_ENVELOPE_BYTES:
            raise history_refused()
        return self


def grant_page_size(page: GrantHistoryPage, records: list[bytes]) -> int:
    return object_size(
        {
            "snapshot": grant_snapshot_size(page.snapshot),
            "start": grant_position_size(page.start),
            "grants": array_size(len(record) for record in records),
            "end": grant_position_size(page.end),
            "complete": boolean_size(page.complete),
        }
    )


class GrantHistoryProgress(BoundaryModel):
    snapshot: GrantHistorySnapshot
    accepted: GrantHistoryPosition
    complete: StrictBool

    @model_validator(mode="after")
    def progress_within_snapshot(self) -> Self:
        if not self.snapshot.holds(self.accepted):
            raise history_refused()
        if self.complete != (self.accepted == self.snapshot.tip()):
            raise history_refused()
        return self


class ClosureSnapshot(BoundaryModel):
    version: Literal[1]
    namespace_id: Identifier
    attempt_id: Identifier
    execution_domain_id: Identifier
    generation: Count
    grant_snapshot: GrantHistorySnapshot
    count: Count
    digest: Sha256

    @model_validator(mode="after")
    def scope_agrees(self) -> Self:
        grants = self.grant_snapshot
        if (grants.namespace_id, grants.attempt_id, grants.execution_domain_id) != (
            self.namespace_id,
            self.attempt_id,
            self.execution_domain_id,
        ):
            raise history_refused()
        if self.count == 0 and self.digest != self.seed():
            raise history_refused()
        return self

    def seed(self) -> str:
        return chain_seed(
            CLOSURE_LABEL, self.namespace_id, self.attempt_id, self.execution_domain_id
        )

    def tip(self) -> "ClosurePosition":
        return ClosurePosition(
            sequence=self.count, count=self.count, prefix_digest=self.digest
        )

    def holds(self, position: "ClosurePosition") -> bool:
        if position.count > self.count:
            return False
        return position.count > 0 or position.prefix_digest == self.seed()


class ClosurePosition(BoundaryModel):
    sequence: Count
    count: Count
    prefix_digest: Sha256

    @model_validator(mode="after")
    def sequence_is_count(self) -> Self:
        if self.sequence != self.count:
            raise history_refused()
        return self


class EvidenceDisposition(BoundaryModel):
    sequence: Count
    item_digest: Sha256
    status: Literal["accepted", "refused"]
    reason: Literal["evidence_unbound", "evidence_conflict"] | None

    @model_validator(mode="after")
    def reason_matches_status(self) -> Self:
        if (self.status == "refused") != (self.reason is not None):
            raise history_refused()
        return self


class ResumeRead(BoundaryModel):
    kind: Literal["resume"]


class BeginRead(BoundaryModel):
    kind: Literal["begin"]
    snapshot: GrantHistorySnapshot
    expected_previous_generation: Count


class AcknowledgeRead(BoundaryModel):
    kind: Literal["acknowledge"]
    generation: Count
    evidence_snapshot: ClosureSnapshot
    end: ClosurePosition
    dispositions: Annotated[
        tuple[EvidenceDisposition, ...], Field(max_length=MAX_PAGE_ROWS)
    ]

    @model_validator(mode="after")
    def dispositions_within_end(self) -> Self:
        if self.generation != self.evidence_snapshot.generation:
            raise history_refused()
        if not self.evidence_snapshot.holds(self.end):
            raise history_refused()
        sequences = [disposition.sequence for disposition in self.dispositions]
        if any(later <= earlier for earlier, later in pairwise(sequences)):
            raise history_refused()
        if sequences and (sequences[0] < 1 or sequences[-1] > self.end.sequence):
            raise history_refused()
        return self


def read_kind(value: object) -> object:
    if isinstance(value, Mapping):
        return cast("Mapping[object, object]", value).get("kind")
    return getattr(value, "kind", None)


RecoveryRead = Annotated[
    Annotated[ResumeRead, Tag("resume")]
    | Annotated[BeginRead, Tag("begin")]
    | Annotated[AcknowledgeRead, Tag("acknowledge")],
    Discriminator(
        read_kind,
        custom_error_type="history_invalid",
        custom_error_message="the history is invalid",
    ),
]


RECOVERY_READ_ADAPTER: Final[TypeAdapter[RecoveryRead]] = TypeAdapter(
    RecoveryRead, config=ConfigDict(hide_input_in_errors=True)
)


class RecoveryProgress(BoundaryModel):
    generation: Count
    snapshot: GrantHistorySnapshot
    scanned: Count
    phase: Literal["awaiting_input", "scanning", "output", "delivered"]

    @model_validator(mode="after")
    def scanned_within_snapshot(self) -> Self:
        if self.scanned > self.snapshot.count:
            raise history_refused()
        return self


class RecoveryEvidence(BoundaryModel):
    attempt_id: Identifier
    execution_domain_id: Identifier
    handle: Handle | None
    retired: StrictBool
    termination: TerminationReceipt | None
    closed_dispatchers: ClosureSnapshot
    normal_completion: NormalCompletionQuiescence | None
    publication_outcome: PublicationOutcome
    observed_at: Instant

    @model_validator(mode="after")
    def parts_agree(self) -> Self:
        if self.termination is not None and not self.retired:
            raise history_refused()
        scope = (self.attempt_id, self.execution_domain_id)
        parts = [
            part
            for part in (
                self.handle,
                self.termination,
                self.normal_completion,
                self.closed_dispatchers,
            )
            if part is not None
        ]
        if any((part.attempt_id, part.execution_domain_id) != scope for part in parts):
            raise history_refused()
        return self


def closure_snapshot_size(snapshot: ClosureSnapshot) -> int:
    return object_size(
        {
            "version": 1,
            "namespace_id": text_size(snapshot.namespace_id),
            "attempt_id": text_size(snapshot.attempt_id),
            "execution_domain_id": text_size(snapshot.execution_domain_id),
            "generation": integer_size(snapshot.generation),
            "grant_snapshot": grant_snapshot_size(snapshot.grant_snapshot),
            "count": integer_size(snapshot.count),
            "digest": DIGEST_SIZE,
        }
    )


def closure_position_size(position: ClosurePosition) -> int:
    return object_size(
        {
            "sequence": integer_size(position.sequence),
            "count": integer_size(position.count),
            "prefix_digest": DIGEST_SIZE,
        }
    )


def observation_metadata_size(observation: RecoveryEvidence | None) -> int:
    if observation is None:
        return NULL_SIZE
    return object_size(
        {
            "attempt_id": text_size(observation.attempt_id),
            "execution_domain_id": text_size(observation.execution_domain_id),
            "handle": handle_metadata_size(observation.handle),
            "retired": boolean_size(observation.retired),
            "termination": termination_size(observation.termination),
            "closed_dispatchers": closure_snapshot_size(observation.closed_dispatchers),
            "normal_completion": normal_completion_size(observation.normal_completion),
            "publication_outcome": outcome_metadata_size(
                observation.publication_outcome
            ),
            "observed_at": INSTANT_SIZE,
        }
    )


class RecoveryPage(BoundaryModel):
    generation: Count
    evidence_snapshot: ClosureSnapshot
    start: ClosurePosition
    closed_dispatchers: Annotated[
        tuple[DispatcherClosure, ...], Field(max_length=MAX_PAGE_ROWS)
    ]
    end: ClosurePosition
    complete: StrictBool
    observation: RecoveryEvidence | None

    @model_validator(mode="after")
    def page_extends_its_start(self) -> Self:
        snapshot = self.evidence_snapshot
        if self.generation != snapshot.generation:
            raise history_refused()
        if not snapshot.holds(self.start) or not snapshot.holds(self.end):
            raise history_refused()
        scope = (snapshot.attempt_id, snapshot.execution_domain_id)
        if any(
            (closure.quiescence.attempt_id, closure.quiescence.execution_domain_id)
            != scope
            for closure in self.closed_dispatchers
        ):
            raise history_refused()
        records = [record_bytes(closure) for closure in self.closed_dispatchers]
        count = self.start.count + len(records)
        expected = ClosurePosition(
            sequence=count,
            count=count,
            prefix_digest=extend(self.start.prefix_digest, records),
        )
        if self.end != expected:
            raise history_refused()
        if self.complete != (self.end == snapshot.tip()):
            raise history_refused()
        if not records and not self.complete:
            raise history_refused()
        if self.complete != (self.observation is not None):
            raise history_refused()
        if self.observation is not None and (
            self.observation.closed_dispatchers != snapshot
        ):
            raise history_refused()
        if observation_metadata_size(self.observation) > (
            MAX_OBSERVATION_METADATA_BYTES
        ):
            raise history_refused()
        if recovery_page_size(self, records) > MAX_ENVELOPE_BYTES:
            raise history_refused()
        return self


def recovery_page_size(page: RecoveryPage, records: list[bytes]) -> int:
    return object_size(
        {
            "generation": integer_size(page.generation),
            "evidence_snapshot": closure_snapshot_size(page.evidence_snapshot),
            "start": closure_position_size(page.start),
            "closed_dispatchers": array_size(len(record) for record in records),
            "end": closure_position_size(page.end),
            "complete": boolean_size(page.complete),
            "observation": observation_metadata_size(page.observation),
        }
    )


def bounded_raw(raw: bytes) -> bytes:
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise HistoryEnvelopeExceeded
    return raw


def read_grant_history_page(raw: bytes) -> GrantHistoryPage:
    return GrantHistoryPage.model_validate_json(bounded_raw(raw))


def read_recovery_read(raw: bytes) -> AcknowledgeRead | BeginRead | ResumeRead:
    return RECOVERY_READ_ADAPTER.validate_json(bounded_raw(raw))
