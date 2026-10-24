import base64
import json
from collections.abc import Callable
from typing import Final

from pydantic import BaseModel, ValidationError

from kora.ir.json import MAX_CANONICAL_BYTES
from kora.ir.vcs_ref_json import MAX_JSON_BYTES
from kora.protocols.evidence import ClosureEvidence, DispatcherClosure
from kora.protocols.history import (
    CLOSURE_LABEL,
    GRANT_HISTORY_LABEL,
    GrantHistoryPage,
    RecoveryEvidence,
    RecoveryPage,
    chain_seed,
    extend,
    record_bytes,
)
from kora.protocols.identity import GrantBinding
from kora.protocols.report import MAX_ERROR_BYTES, AttemptError, TerminalReport
from kora.protocols.runtime import AdoptedSession, Workload
from kora.protocols.vcs import MAX_FILE_CONTENT_BYTES, MAX_STDIN_BYTES
from tests.protocols import runtime_documents as documents
from tests.protocols.harness_documents import SESSION_SPEC
from tests.protocols.runtime_documents import (
    WIDEST_SCOPE,
    widest_closure,
    widest_grant,
)

Action = Callable[[], object]
JSON_OBJECT_ROOM: Final[int] = MAX_CANONICAL_BYTES - 64
CHAIN_LENGTHS: Final[tuple[int, ...]] = (1, 128, 1000, 10_000)
CONTROL_FRAME_BYTES: Final[int] = 1_048_576
BODY_CAP_BYTES: Final[int] = 4_000_000


def encoded(document: dict[str, object]) -> bytes:
    return json.dumps(document).encode("utf-8")


def full_object() -> dict[str, object]:
    return {"blob": "x" * JSON_OBJECT_ROOM}


def full_reference() -> dict[str, object]:
    return {**documents.VCS_REF, "diff_stat": "d" * (MAX_JSON_BYTES - 4096)}


def full_plan_files(area: str) -> list[dict[str, object]]:
    content = base64.urlsafe_b64encode(b"x" * MAX_FILE_CONTENT_BYTES).decode()
    return [
        documents.plan_file(area=area, path=f"helpers/file-{index}", content=content)
        for index in range(2)
    ]


def maximal_workload() -> dict[str, object]:
    stdin = base64.urlsafe_b64encode(b"x" * MAX_STDIN_BYTES).decode()
    return documents.workload(
        execution_request=full_object(),
        resolved_config=full_object(),
        limits=full_object(),
        checkout=documents.checkout_plan(files=full_plan_files("control")[:1]),
        publish=documents.publish_plan(
            files=full_plan_files("publication")[:1],
            export=[documents.plan_step(cwd="candidate", capture=None, stdin=stdin)],
        ),
        session_spec={**SESSION_SPEC, "prompt": "p" * (MAX_CANONICAL_BYTES - 64)},
    )


def maximal_adopted_session() -> dict[str, object]:
    return {
        "handle": documents.handle(data=full_object()),
        "phase": "awaiting_authorization",
        "prepared": documents.prepared_publication(
            changed_paths=[
                f"src/{index:06d}.py".ljust(255, "p") for index in range(2000)
            ]
        ),
        "journal_next": 7,
        "observational": False,
    }


def maximal_recovery_evidence() -> dict[str, object]:
    return documents.recovery_evidence(
        [documents.closure()],
        handle=documents.handle(data=full_object()),
        publication_outcome=documents.outcome(
            state="completed", operation_id="op-1", ref=full_reference()
        ),
    )


def maximal_report() -> dict[str, object]:
    closures = [widest_closure(index) for index in range(1, 59)]
    for closure in closures:
        quiescence = documents.member(closure, "quiescence")
        quiescence.update(
            attempt_id=documents.ATTEMPT, execution_domain_id=documents.DOMAIN
        )
    message = "m" * 4096
    room = MAX_ERROR_BYTES - 4096 - 64
    return documents.terminal_report(
        outcome="failed",
        error_class="command_failed",
        error=documents.attempt_error(message=message, details={"t": "x" * room}),
        evidence=documents.closure_evidence(
            dispatchers=closures,
            publication=documents.outcome(
                state="completed", operation_id="op-1", ref=full_reference()
            ),
        ),
    )


MAXIMAL: Final[dict[str, tuple[type[BaseModel], Callable[[], dict[str, object]]]]] = {
    "workload": (Workload, maximal_workload),
    "adopted_session": (AdoptedSession, maximal_adopted_session),
    "recovery_evidence": (RecoveryEvidence, maximal_recovery_evidence),
    "terminal_report": (TerminalReport, maximal_report),
}


def validating(model: type[BaseModel], payload: bytes) -> Action:
    return lambda: model.model_validate_json(payload)


def refusing(model: type[BaseModel], payload: bytes) -> Action:
    def action() -> object:
        try:
            model.model_validate_json(payload)
        except ValidationError as error:
            return error.error_count()
        raise AssertionError("the refusal workload was accepted")

    return action


def small_grants() -> list[dict[str, object]]:
    return [documents.grant(epoch) for epoch in range(1, 129)]


def widest_grants(count: int) -> list[dict[str, object]]:
    return [widest_grant(index) for index in range(1, count + 1)]


def widest_closures(count: int) -> list[dict[str, object]]:
    return [widest_closure(index) for index in range(1, count + 1)]


def broken_chain() -> dict[str, object]:
    page = documents.grant_page(widest_grants(67), 0, 67, WIDEST_SCOPE)
    grants = documents.members(page, "grants")
    grants[-1] = {**grants[-1], "worker_id": "changed"}
    return page


def oversized_error() -> dict[str, object]:
    return documents.attempt_error(details={"t": "x" * MAX_ERROR_BYTES})


def relayed_closures() -> dict[str, object]:
    closures = widest_closures(58)
    for closure in closures:
        documents.member(closure, "quiescence").update(
            attempt_id=documents.ATTEMPT, execution_domain_id=documents.DOMAIN
        )
    return documents.closure_evidence(runtime=None, dispatchers=closures)


def page_rows() -> dict[str, tuple[Action, int]]:
    small = small_grants()
    widest = widest_grants(68)
    closures = widest_closures(58)
    return {
        "grant_page_128_small": (
            validating(GrantHistoryPage, encoded(documents.grant_page(small, 0, 128))),
            128,
        ),
        "grant_page_67_near_cap": (
            validating(
                GrantHistoryPage,
                encoded(documents.grant_page(widest[:67], 0, 67, WIDEST_SCOPE)),
            ),
            67,
        ),
        "closure_evidence_58_near_cap": (
            validating(ClosureEvidence, encoded(relayed_closures())),
            58,
        ),
        "recovery_page_57_near_cap": (
            validating(
                RecoveryPage,
                encoded(documents.recovery_page(closures, 0, 57, WIDEST_SCOPE)),
            ),
            57,
        ),
        "recovery_final_page_43_near_cap": (
            validating(
                RecoveryPage,
                encoded(documents.recovery_page(closures[:43], 0, 43, WIDEST_SCOPE)),
            ),
            43,
        ),
        "refused_page_68_near_cap": (
            refusing(
                GrantHistoryPage,
                encoded(documents.grant_page(widest, 0, 68, WIDEST_SCOPE)),
            ),
            68,
        ),
        "refused_broken_chain": (
            refusing(GrantHistoryPage, encoded(broken_chain())),
            67,
        ),
        "refused_oversized_error": (
            refusing(AttemptError, encoded(oversized_error())),
            1,
        ),
    }


def chain_rows() -> dict[str, tuple[Action, int]]:
    grant = record_bytes(GrantBinding.model_validate(widest_grant(1)))
    closure = record_bytes(DispatcherClosure.model_validate(widest_closure(1)))
    seeds = {
        "grant": (chain_seed(GRANT_HISTORY_LABEL, "ns", "att", "dom"), grant),
        "closure": (chain_seed(CLOSURE_LABEL, "ns", "att", "dom"), closure),
    }
    return {
        f"chain_{kind}_{length}": (
            chain_action(seed, record, length),
            length,
        )
        for kind, (seed, record) in seeds.items()
        for length in CHAIN_LENGTHS
    }


def chain_action(seed: str, record: bytes, length: int) -> Action:
    records = [record] * length
    return lambda: extend(seed, records)
