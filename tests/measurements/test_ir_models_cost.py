import gc
import time
import tracemalloc
from collections.abc import Callable, Iterator

import pytest
from pydantic import ValidationError

from kora.ir import ToolPolicy
from kora.ir.json import DEFAULT_MAX_DEPTH, MAX_CANONICAL_BYTES, JsonObject, JsonValue
from kora.protocols.errors import KoraProtocolError
from kora.protocols.metadata import parse_config_schema
from tests.measurements.harness import Report, Sample, report_lifecycle

pytestmark = pytest.mark.measurement

WARM_UP: int = 3
MEASURED: int = 30
PATTERNS: int = 250_000
FAILURE_CYCLES: int = 1_000
RETAINED_LIMIT_BYTES: int = 65_536
LEAF_BYTES: int = 1024
CONFIG_SCHEMA_BYTES: int = 65_536


@pytest.fixture(scope="module")
def report() -> Iterator[Report]:
    yield from report_lifecycle()


def leaves(total: int) -> list[JsonValue]:
    leaf = "a" * (LEAF_BYTES - 3)
    return [leaf for _ in range(total // LEAF_BYTES)]


def deep_extension(total: int) -> JsonObject:
    document: JsonObject = {"leaves": leaves(total)}
    for _ in range(DEFAULT_MAX_DEPTH - 3):
        document = {"n": document}
    return document


def accepted(action: Callable[[], object]) -> bool:
    try:
        action()
    except ValidationError:
        return False
    return True


def validate(document: dict[str, object]) -> Callable[[], object]:
    return lambda: ToolPolicy.model_validate(document)


def protocol_error(details: JsonObject) -> Callable[[], object]:
    return lambda: KoraProtocolError("failed", "failed", False, details)


def config_schema(total: int) -> Callable[[], object]:
    document: JsonObject = {"type": "object", "x-leaves": leaves(total)}
    return lambda: parse_config_schema(document)


WORKLOADS: dict[str, tuple[Callable[[], object], int, bool]] = {
    "patterns_250k": (validate({"allow": ["a"] * PATTERNS}), PATTERNS, True),
    "extension_1mib_depth16": (
        validate({"x-a": deep_extension(MAX_CANONICAL_BYTES - 4096)}),
        1,
        True,
    ),
    "extension_over_1mib": (
        validate({"x-a": deep_extension(MAX_CANONICAL_BYTES + 4096)}),
        1,
        False,
    ),
    "refused_last_pattern": (
        validate({"allow": ["a"] * (PATTERNS - 1) + [1]}),
        PATTERNS,
        False,
    ),
    "protocol_error_details_1mib": (
        protocol_error({"leaves": leaves(MAX_CANONICAL_BYTES - 4096)}),
        1,
        True,
    ),
    "config_schema_64kib": (config_schema(CONFIG_SCHEMA_BYTES), 1, True),
}


@pytest.mark.parametrize("workload", sorted(WORKLOADS))
def test_ir_model_validation_cost(report: Report, workload: str) -> None:
    action, count, expected = WORKLOADS[workload]
    for _ in range(WARM_UP):
        accepted(action)

    for repeat in range(MEASURED):
        start = time.perf_counter()
        outcome = accepted(action)
        report.samples.append(
            Sample(workload, repeat, count, time.perf_counter() - start)
        )
        assert outcome is expected
    gc.collect()
    tracemalloc.start()
    accepted(action)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    report.environment[f"{workload}_peak_bytes"] = peak


FAILURES: tuple[dict[str, object], ...] = (
    {"allow": ["Read"], "unknown": "value"},
    {"deny": [7]},
    {"x-a": float("nan")},
    {"x-a": "a" * MAX_CANONICAL_BYTES},
)


def refuse_every_failure() -> None:
    for document in FAILURES:
        assert not accepted(validate(document))


def test_repeated_failures_return_to_baseline(report: Report) -> None:
    for _ in range(WARM_UP):
        refuse_every_failure()
    gc.collect()
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]

    start = time.perf_counter()
    for _ in range(FAILURE_CYCLES):
        refuse_every_failure()
    elapsed = time.perf_counter() - start
    gc.collect()
    retained = tracemalloc.get_traced_memory()[0] - baseline
    tracemalloc.stop()

    report.samples.append(
        Sample("failure_cycles", 0, FAILURE_CYCLES * len(FAILURES), elapsed)
    )
    report.environment["failure_cycles_retained_bytes"] = retained
    report.gates["failure_cycles_return_to_baseline"] = retained < RETAINED_LIMIT_BYTES
    assert retained < RETAINED_LIMIT_BYTES
