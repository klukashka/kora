from typing import Final

import pytest
from pydantic import BaseModel, ValidationError

from kora.ir.errors import safe_field_errors
from kora.protocols.harness import (
    EVENT_ADAPTER,
    MAX_SAMPLE_ITEMS,
    MAX_TEXT_BYTES,
    MAX_USAGE_ROWS,
    ErrorData,
    PermissionDeniedData,
    ProviderLimitedData,
    ProviderRetryingData,
    ResultData,
    SessionResult,
    SessionStartedData,
    ToolCallData,
    ToolResultData,
    TurnData,
    UsageData,
)
from tests.protocols.harness_documents import (
    DENIAL,
    EVENT_DATA,
    REFUSED_MARKER,
    UNCONFIRMED,
    event,
    session_result,
    usage_row,
)

DATA_MODELS: Final[tuple[type[BaseModel], ...]] = (
    SessionStartedData,
    TurnData,
    ToolCallData,
    ToolResultData,
    PermissionDeniedData,
    UsageData,
    ProviderLimitedData,
    ProviderRetryingData,
    ResultData,
    ErrorData,
)
KNOWN: Final[frozenset[str]] = frozenset(
    {"i", "at", "kind", "data", "items", "total", "source", "value"}
    | {name for model in DATA_MODELS for name in model.model_fields}
    | set(SessionResult.model_fields)
)
OVERSIZED_TEXT: Final[str] = REFUSED_MARKER + "x" * MAX_TEXT_BYTES
REFUSED_EVENT_KEYS: Final[list[dict[str, object]]] = [
    event("turn", **{REFUSED_MARKER: True}),
    {**event("turn"), REFUSED_MARKER: True},
]
REFUSED_EVENTS: Final[list[dict[str, object]]] = [
    {**event("turn"), "data": EVENT_DATA["tool_call"]},
    {**event("usage"), "data": EVENT_DATA["provider_limited"]},
    {**event("turn"), "kind": REFUSED_MARKER},
    {key: value for key, value in event("turn").items() if key != "kind"},
    {**event("turn"), "kind": [REFUSED_MARKER]},
    event("turn", text=OVERSIZED_TEXT),
    event("tool_result", content=OVERSIZED_TEXT),
    event("error", text=OVERSIZED_TEXT),
    event("turn", text="\ud800" + REFUSED_MARKER),
    event("permission_denied", pattern=f"namespace.allow.{REFUSED_MARKER}"),
    event("permission_denied", pattern="layer.deny.0"),
    event("permission_denied", code=REFUSED_MARKER),
    event("permission_denied", source="harness", code="not_allowed"),
    event("permission_denied", source="policy", code="harness_rule"),
    event("provider_limited", vendor_group=f"{REFUSED_MARKER} group"),
    event("provider_limited", retry_after=REFUSED_MARKER),
    event("provider_limited", retry_after=1),
    event("provider_retrying", retry_after="2026-10-23T00:00:00"),
    event("result", subtype=REFUSED_MARKER.upper()),
    event("error", code=f"{REFUSED_MARKER} code"),
    event("turn", truncated=1),
]
REFUSED_RESULT_KEYS: Final[list[dict[str, object]]] = [
    session_result(denials={"items": [{**DENIAL, REFUSED_MARKER: True}], "total": 1}),
    session_result(error_summary={"source": "harness", REFUSED_MARKER: True}),
    session_result(**{REFUSED_MARKER: True}),
]
REFUSED_RESULTS: Final[list[dict[str, object]]] = [
    session_result(usage=[usage_row()] * (MAX_USAGE_ROWS + 1)),
    session_result(denials={"items": [DENIAL] * (MAX_SAMPLE_ITEMS + 1), "total": 2000}),
    session_result(
        unconfirmed_calls={
            "items": [UNCONFIRMED] * (MAX_SAMPLE_ITEMS + 1),
            "total": 2000,
        }
    ),
    session_result(denials={"items": [DENIAL, DENIAL], "total": 1}),
    session_result(unconfirmed_calls={"items": [UNCONFIRMED], "total": 0}),
    session_result(
        unconfirmed_calls={
            "items": [{**UNCONFIRMED, "tool": REFUSED_MARKER + " tool"}],
            "total": 1,
        }
    ),
    session_result(
        output={"source": "result_text", "value": {"a": [OVERSIZED_TEXT] * 32}}
    ),
    session_result(output={"source": REFUSED_MARKER, "value": None}),
    session_result(api_error_status=600),
    session_result(error_class=REFUSED_MARKER),
    session_result(stop_reason=f"{REFUSED_MARKER} stop"),
]


def safe_rendering(error: ValidationError) -> str:
    return repr(safe_field_errors(error, KNOWN, "Invalid harness value."))


@pytest.mark.parametrize("kind", sorted(EVENT_DATA))
def test_every_event_kind_round_trips(kind: str) -> None:
    document = event(kind)

    parsed = EVENT_ADAPTER.validate_python(document)
    restored = EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(parsed))

    assert restored == parsed
    assert EVENT_ADAPTER.dump_python(parsed, mode="json") == document


@pytest.mark.parametrize("document", REFUSED_EVENTS + REFUSED_EVENT_KEYS)
def test_session_event_data_is_closed(document: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as raised:
        EVENT_ADAPTER.validate_python(document)

    assert REFUSED_MARKER not in safe_rendering(raised.value)


@pytest.mark.parametrize("document", REFUSED_EVENTS)
def test_refused_event_values_stay_out_of_errors(document: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as raised:
        EVENT_ADAPTER.validate_python(document)

    assert REFUSED_MARKER not in str(raised.value)


def test_session_result_round_trips() -> None:
    document = session_result()

    parsed = SessionResult.model_validate(document)
    restored = SessionResult.model_validate_json(parsed.model_dump_json())

    assert restored == parsed
    assert parsed.model_dump(mode="json") == document


@pytest.mark.parametrize("document", REFUSED_RESULTS + REFUSED_RESULT_KEYS)
def test_session_result_shape(document: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as raised:
        SessionResult.model_validate(document)

    assert REFUSED_MARKER not in safe_rendering(raised.value)


@pytest.mark.parametrize("document", REFUSED_RESULTS)
def test_refused_result_values_stay_out_of_errors(document: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as raised:
        SessionResult.model_validate(document)

    assert REFUSED_MARKER not in str(raised.value)


def test_session_result_accepts_its_bounds() -> None:
    document = session_result(
        usage=[
            usage_row(observation_sequence=index) for index in range(MAX_USAGE_ROWS)
        ],
        denials={"items": [DENIAL] * MAX_SAMPLE_ITEMS, "total": MAX_SAMPLE_ITEMS},
        unconfirmed_calls={"items": [UNCONFIRMED] * MAX_SAMPLE_ITEMS, "total": 5000},
    )

    parsed = SessionResult.model_validate(document)

    assert len(parsed.usage) == MAX_USAGE_ROWS
    assert parsed.unconfirmed_calls.total == 5000
