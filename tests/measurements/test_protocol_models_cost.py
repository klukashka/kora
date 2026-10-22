import gc
import json
import time
import tracemalloc
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from kora.conformance.capture import captured
from kora.conformance.common import CONFORMANCE_COMPONENT
from kora.observability import LoggingSettings, ProcessDiagnostics
from kora.protocols.identity import GrantBinding
from kora.protocols.secret_value import SecretScope
from tests.measurements.harness import Report, Sample, report_lifecycle
from tests.support.observability import run_python

pytestmark = pytest.mark.measurement

INDEPENDENT_RUNS: int = 3
MEASURED: int = 30
WARM_UP: int = 5
GRANTS: int = 64
REGISTRATION_CYCLES: int = 3
HEAD_BYTES: int = 8
HEAP_GROWTH_LIMIT_BYTES: int = 262_144
IMPORT_PROBE: str = (
    "import json, sys, time\n"
    "start = time.perf_counter()\n"
    "import kora.protocols.common, kora.protocols.identity, kora.protocols.secrets\n"
    "print(json.dumps({'seconds': time.perf_counter() - start, "
    "'modules': len(sys.modules)}))\n"
)
REGISTRATION_WORKLOADS: dict[str, tuple[int, int, bool]] = {
    "plain_8b": (250, 8, False),
    "escaped_8b": (85, 8, True),
    "plain_1kib": (32, 1024, False),
    "escaped_1kib": (12, 1024, True),
    "plain_16kib": (3, 16384, False),
    "escaped_16kib": (1, 16384, True),
}


@pytest.fixture
def report() -> Iterator[Report]:
    yield from report_lifecycle()


def utc_now() -> datetime:
    return datetime.now(UTC)


def value_of(index: int, size: int, escaped: bool) -> str:
    head = ('"' if escaped else "v") + f"{index:0{HEAD_BYTES - 1}d}"
    filler = "".join(
        '"' if escaped and position % 64 == 0 else "x"
        for position in range(size - HEAD_BYTES)
    )
    return head + filler


def expected_forms(count: int, escaped: bool) -> int:
    return count * (2 if escaped else 1)


def binding_document() -> bytes:
    process = {
        "host_id": "host",
        "boot_id": "boot",
        "pid_namespace": "pidns",
        "pid": 1000,
        "start_time": 1,
        "leads_group": True,
    }
    grants = [
        {
            "dispatcher_id": f"dispatcher-{number}",
            "worker_id": "worker",
            "incarnation_id": "incarnation",
            "lease_epoch": number,
            "execution_domain_id": "domain",
            "process": process,
        }
        for number in range(GRANTS)
    ]
    return json.dumps(grants).encode("utf-8")


def test_protocol_import_cost(report: Report) -> None:
    for run in range(INDEPENDENT_RUNS):
        result = run_python(["-I", "-c", IMPORT_PROBE], {})

        assert result.code == 0, result.stderr
        measured = json.loads(result.stdout)
        report.samples.append(Sample("cold_import", run, 1, measured["seconds"]))
        report.environment[f"run_{run}_modules"] = measured["modules"]


def test_grant_binding_validation_cost(report: Report) -> None:
    document = binding_document()
    for _ in range(WARM_UP):
        [GrantBinding.model_validate(item) for item in json.loads(document)]
    tracemalloc.start()

    for repeat in range(MEASURED):
        start = time.perf_counter()
        [GrantBinding.model_validate(item) for item in json.loads(document)]
        report.samples.append(
            Sample("grant_bindings_64", repeat, GRANTS, time.perf_counter() - start)
        )
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    report.environment["grant_bindings_peak_bytes"] = peak


def admit_and_release(
    diagnostics: ProcessDiagnostics, values: list[str]
) -> tuple[int, int]:
    scope = SecretScope(diagnostics)
    for value in values:
        scope.admit(value)
    registry = diagnostics.registry
    state = registry.count(), registry.retained_bytes()
    scope.release()
    return state


@pytest.mark.parametrize("workload", sorted(REGISTRATION_WORKLOADS))
def test_secret_registration_cost(report: Report, workload: str) -> None:
    count, size, escaped = REGISTRATION_WORKLOADS[workload]
    values = [value_of(index, size, escaped) for index in range(count)]
    with captured(
        CONFORMANCE_COMPONENT, LoggingSettings(), "measurement", utc_now
    ) as capture:
        diagnostics = capture.diagnostics
        forms, retained = admit_and_release(diagnostics, values)

        for repeat in range(REGISTRATION_CYCLES):
            start = time.perf_counter()
            forms, retained = admit_and_release(diagnostics, values)
            report.samples.append(
                Sample(workload, repeat, count, time.perf_counter() - start)
            )
        gc.collect()
        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]
        admit_and_release(diagnostics, values)
        gc.collect()
        growth = tracemalloc.get_traced_memory()[0] - baseline
        tracemalloc.stop()

        report.environment[f"{workload}_forms"] = forms
        report.environment[f"{workload}_retained_bytes"] = retained
        report.environment[f"{workload}_heap_growth_bytes"] = growth
        assert len(set(values)) == count
        assert forms == expected_forms(count, escaped)
        assert diagnostics.registry.count() == 0
        assert growth < HEAP_GROWTH_LIMIT_BYTES
