import json
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

import kora.conformance.common as common_cases
import kora.conformance.secrets as secrets_cases
from kora.conformance.capture import Capture, captured
from kora.conformance.common import CONFORMANCE_COMPONENT
from kora.observability import LoggingSettings, current_diagnostics
from tests.conformance.providers import (
    MISSING,
    REFERENCE,
    VALUE,
    EchoingSecretsProvider,
    MutableCapabilitiesProvider,
    ReferenceSecretsProvider,
    echoing_exercise,
    exercise,
)
from tests.support.observability import run_python

CYCLES: int = 20
WARM_UP: int = 2
PROBE: Path = Path(__file__).with_name("stuck_capture_probe.py")


def utc_now() -> datetime:
    return datetime.now(UTC)


def run_case(case: Callable[[Capture], None]) -> None:
    with captured(
        CONFORMANCE_COMPONENT, LoggingSettings(), "selftest", utc_now
    ) as capture:
        case(capture)


def descriptor_count() -> int:
    return len(os.listdir("/dev/fd"))


def resolution(capture: Capture) -> None:
    secrets_cases.test_secrets_resolution_returns_a_masked_value(
        ReferenceSecretsProvider(), (REFERENCE, VALUE), capture
    )


def bounded_failure(capture: Capture) -> None:
    common_cases.test_failure_is_a_bounded_protocol_error(exercise, capture)


def test_cases_fail_for_a_broken_provider() -> None:
    with pytest.raises(pytest.fail.Exception):
        common_cases.test_config_schema_is_an_object_schema(
            MutableCapabilitiesProvider()
        )
    with pytest.raises(pytest.fail.Exception):
        run_case(
            lambda capture: common_cases.test_failure_is_a_bounded_protocol_error(
                echoing_exercise, capture
            )
        )
    with pytest.raises(pytest.fail.Exception):
        run_case(
            lambda capture: (
                secrets_cases.test_secrets_missing_reference_is_a_bounded_error(
                    EchoingSecretsProvider(), MISSING, capture
                )
            )
        )


def test_cases_release_every_resource_after_repeated_runs() -> None:
    for _ in range(WARM_UP):
        run_case(resolution)
    threads = threading.active_count()
    descriptors = descriptor_count()

    for _ in range(CYCLES):
        run_case(resolution)
        run_case(bounded_failure)

    assert threading.active_count() == threads
    assert descriptor_count() == descriptors
    assert current_diagnostics() is None


def test_a_stuck_capture_is_bounded_and_reported() -> None:
    result = run_python(["-I", str(PROBE)], {})

    assert result.code == 0, result.stderr
    assert json.loads(result.stdout) == {
        "timed_out": True,
        "refused": True,
        "held": 1,
    }
