import dataclasses
import gc
import json
import os
import threading
import tracemalloc
from collections.abc import Callable
from pathlib import Path

import pytest

import kora.conformance.common as common_cases
import kora.conformance.policy as policy_cases
import kora.conformance.secrets as secrets_cases
from kora.conformance.capture import (
    LEFTOVERS,
    Capture,
    CaptureOverflow,
    Leftover,
    Pipe,
    captured,
)
from kora.conformance.common import CONFORMANCE_COMPONENT, MARKER, utc_now
from kora.observability import LoggingSettings, configure_process, current_diagnostics
from tests.conformance.policies import (
    AllowOverridesDenyPolicy,
    AnyDenyPolicy,
    ContradictingReasonPolicy,
    ExactProtectedPathPolicy,
    FirstDenyOnlyPolicy,
    HardcodedTestsPolicy,
    LooseDecisionPolicy,
    NodeAllowIgnoringPolicy,
    NonBoolAllowPolicy,
    ReferencePolicy,
    ShellOnlyRulesPolicy,
    SilentValidatePolicy,
    SubagentProtectionSkippingPolicy,
    SubagentSkippingPolicy,
    ToolCachingPolicy,
    echoing_policy_exercise,
    policy_exercise,
    reference_calls,
)
from tests.conformance.providers import (
    FORBIDDEN,
    MISSING,
    REFERENCE,
    RESOLVING,
    VALUE,
    ChainedSecretsProvider,
    EchoingSecretsProvider,
    LeakingNamesSecretsProvider,
    LeakingSecretsProvider,
    MutableCapabilitiesProvider,
    NameEchoingSecretsProvider,
    ReferenceSecretsProvider,
    SilentNamesSecretsProvider,
    UnprefixedNamesSecretsProvider,
    WrongValueSecretsProvider,
    echoing_exercise,
    exercise,
    silent_exercise,
)
from tests.support.observability import run_python

CYCLES: int = 20
WARM_UP: int = 2
MAX_RETAINED_BYTES: int = 64 * 1024
PROBE: Path = Path(__file__).with_name("stuck_capture_probe.py")


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


def mutable_capabilities(capture: Capture) -> None:
    common_cases.test_capabilities_are_frozen_and_consistent(
        MutableCapabilitiesProvider()
    )


def array_config_schema(capture: Capture) -> None:
    common_cases.test_config_schema_is_an_object_schema(MutableCapabilitiesProvider())


def echoing_failure(capture: Capture) -> None:
    common_cases.test_failure_is_a_bounded_protocol_error(echoing_exercise, capture)


def silent_failure(capture: Capture) -> None:
    common_cases.test_provider_logs_through_its_context(silent_exercise, capture)


def echoing_missing_reference(capture: Capture) -> None:
    secrets_cases.test_secrets_missing_reference_is_a_bounded_error(
        EchoingSecretsProvider(), MISSING, capture
    )


def echoing_forbidden_reference(capture: Capture) -> None:
    secrets_cases.test_secrets_forbidden_reference_is_refused_like_a_missing_one(
        EchoingSecretsProvider(), FORBIDDEN, capture
    )


def chained_forbidden_reference(capture: Capture) -> None:
    secrets_cases.test_secrets_forbidden_reference_is_refused_like_a_missing_one(
        ChainedSecretsProvider(), FORBIDDEN, capture
    )


def name_echoing_missing_reference(capture: Capture) -> None:
    secrets_cases.test_secrets_missing_reference_is_a_bounded_error(
        NameEchoingSecretsProvider(), MISSING, capture
    )


def leaking_names(capture: Capture) -> None:
    secrets_cases.test_secrets_list_names_returns_names_only(
        LeakingNamesSecretsProvider(), "", (REFERENCE, VALUE)
    )


def silent_names(capture: Capture) -> None:
    secrets_cases.test_secrets_list_names_returns_names_only(
        SilentNamesSecretsProvider(), "", (REFERENCE, VALUE)
    )


def unprefixed_names(capture: Capture) -> None:
    secrets_cases.test_secrets_list_names_returns_names_only(
        UnprefixedNamesSecretsProvider(), "conf-", (REFERENCE, VALUE)
    )


def wrong_value_resolution(capture: Capture) -> None:
    secrets_cases.test_secrets_resolution_returns_a_masked_value(
        WrongValueSecretsProvider(), (REFERENCE, VALUE), capture
    )


def leaking_resolution(capture: Capture) -> None:
    secrets_cases.test_secrets_resolution_returns_a_masked_value(
        LeakingSecretsProvider(), (REFERENCE, VALUE), capture
    )


def unmasked_stream(capture: Capture) -> None:
    secrets_cases.test_secrets_value_is_masked_in_a_captured_stream(
        WrongValueSecretsProvider(), (REFERENCE, VALUE), capture
    )


def policy_deny(capture: Capture) -> None:
    for layer in policy_cases.LAYERS:
        policy_cases.test_policy_deny_in_either_layer_denies(
            ReferencePolicy(), reference_calls(), capture, layer
        )


def policy_protected(capture: Capture) -> None:
    policy_cases.test_policy_protected_path_is_denied(
        ReferencePolicy(), reference_calls(), capture
    )


def policy_failure(capture: Capture) -> None:
    common_cases.test_failure_is_a_bounded_protocol_error(policy_exercise, capture)


def ignored_node_allow_list(capture: Capture) -> None:
    policy_cases.test_policy_allow_list_restricts_in_either_layer(
        NodeAllowIgnoringPolicy(), reference_calls(), capture, "node"
    )


def any_deny_refuses(capture: Capture) -> None:
    policy_cases.test_policy_allows_what_no_rule_refuses(
        AnyDenyPolicy(), reference_calls(), capture
    )


def first_deny_only(capture: Capture) -> None:
    policy_cases.test_policy_deny_in_either_layer_denies(
        FirstDenyOnlyPolicy(), reference_calls(), capture, "namespace"
    )


def subagent_skipped(capture: Capture) -> None:
    policy_cases.test_policy_deny_in_either_layer_denies(
        SubagentSkippingPolicy(), reference_calls(), capture, "node"
    )


def allow_overrides_deny(capture: Capture) -> None:
    policy_cases.test_policy_deny_in_either_layer_denies(
        AllowOverridesDenyPolicy(), reference_calls(), capture, "namespace"
    )


def cached_by_tool(capture: Capture) -> None:
    policy_cases.test_policy_decisions_are_deterministic(
        ToolCachingPolicy(), reference_calls(), capture
    )


def exact_protected_path(capture: Capture) -> None:
    policy_cases.test_policy_protected_path_is_denied(
        ExactProtectedPathPolicy(), reference_calls(), capture
    )


def silent_validate(capture: Capture) -> None:
    policy_cases.test_policy_validate_refuses_a_malformed_policy(
        SilentValidatePolicy(), reference_calls()
    )


def loose_decision(capture: Capture) -> None:
    policy_cases.test_policy_allows_what_no_rule_refuses(
        LooseDecisionPolicy(), reference_calls(), capture
    )


def echoing_policy_failure(capture: Capture) -> None:
    common_cases.test_failure_is_a_bounded_protocol_error(
        echoing_policy_exercise, capture
    )


def subagent_protection_skipped(capture: Capture) -> None:
    policy_cases.test_policy_protected_path_is_denied(
        SubagentProtectionSkippingPolicy(), reference_calls(), capture
    )


def shell_only_rules(capture: Capture) -> None:
    policy_cases.test_policy_deny_in_either_layer_denies(
        ShellOnlyRulesPolicy(), reference_calls(), capture, "namespace"
    )


def hardcoded_tests(capture: Capture) -> None:
    policy_cases.test_policy_protected_path_is_denied(
        HardcodedTestsPolicy(), reference_calls(), capture
    )


def non_bool_allow(capture: Capture) -> None:
    policy_cases.test_policy_allows_what_no_rule_refuses(
        NonBoolAllowPolicy(), reference_calls(), capture
    )


def contradicting_reason(capture: Capture) -> None:
    policy_cases.test_policy_deny_in_either_layer_denies(
        ContradictingReasonPolicy(), reference_calls(), capture, "node"
    )


def no_edit_calls(capture: Capture) -> None:
    policy_cases.test_policy_protected_path_is_denied(
        ReferencePolicy(), dataclasses.replace(reference_calls(), edits=()), capture
    )


BROKEN_CASES: tuple[tuple[Callable[[Capture], None], str], ...] = (
    (mutable_capabilities, "capabilities() returns a frozen dataclass"),
    (array_config_schema, "config_schema has type object"),
    (echoing_failure, "the message carries no input"),
    (silent_failure, "the provider logs through the context's logger"),
    (echoing_missing_reference, "a refused reference is not retryable"),
    (echoing_forbidden_reference, "a refused reference is not retryable"),
    (chained_forbidden_reference, "the error carries no chain"),
    (name_echoing_missing_reference, "the message and details carry no reference"),
    (leaking_names, "no name is a value"),
    (unprefixed_names, "every name has the requested prefix"),
    (silent_names, "the listed prefix names at least one secret"),
    (wrong_value_resolution, "resolve returns the referenced value"),
    (leaking_resolution, "no captured record carries the secret"),
    (unmasked_stream, "every escaped form of the secret is masked"),
    (ignored_node_allow_list, "an allow list that does not match denies"),
    (any_deny_refuses, "a call no rule refuses is allowed"),
    (first_deny_only, "a matching deny denies"),
    (subagent_skipped, "a matching deny denies"),
    (allow_overrides_deny, "a matching deny denies"),
    (cached_by_tool, "interleaved requests keep their own decisions"),
    (exact_protected_path, "a protected path is denied"),
    (silent_validate, "validate refuses the malformed policy"),
    (loose_decision, "decide returns a PolicyDecision"),
    (echoing_policy_failure, "the message carries no input"),
    (subagent_protection_skipped, "a protected path is denied"),
    (shell_only_rules, "a matching deny denies"),
    (hardcoded_tests, "a protected path is denied"),
    (non_bool_allow, "allow is an exact bool"),
    (contradicting_reason, "the reason agrees with the decision"),
    (no_edit_calls, "the adopter supplies at least one file-edit call"),
)


@pytest.mark.parametrize(
    ("case", "message"), BROKEN_CASES, ids=[case.__name__ for case, _ in BROKEN_CASES]
)
def test_cases_fail_for_a_broken_provider(
    case: Callable[[Capture], None], message: str
) -> None:
    failing = pytest.raises(pytest.fail.Exception)

    with failing as raised:
        run_case(case)

    assert str(raised.value) == message
    assert current_diagnostics() is None


def test_a_capture_past_its_cap_fails_and_releases_everything() -> None:
    threads = threading.active_count()
    descriptors = descriptor_count()

    with pytest.raises(CaptureOverflow):
        with captured(
            CONFORMANCE_COMPONENT, LoggingSettings(), "overflow", utc_now, max_bytes=64
        ) as capture:
            capture.diagnostics.logger(CONFORMANCE_COMPONENT).emit(
                RESOLVING, detail=MARKER
            )

    assert threading.active_count() == threads
    assert descriptor_count() == descriptors
    assert current_diagnostics() is None


def test_a_finished_leftover_is_cleared_by_the_next_capture() -> None:
    threads = threading.active_count()
    descriptors = descriptor_count()
    pipe = Pipe.open()
    diagnostics = configure_process(
        CONFORMANCE_COMPONENT, LoggingSettings(), "leftover", utc_now, pipe.write_end
    )
    pipe.reader.start()
    diagnostics.shutdown()
    LEFTOVERS.held.append(Leftover(diagnostics, pipe))

    with captured(CONFORMANCE_COMPONENT, LoggingSettings(), "next", utc_now):
        held = len(LEFTOVERS.held)

    assert held == 0
    assert threading.active_count() == threads
    assert descriptor_count() == descriptors


def policy_validate(capture: Capture) -> None:
    policy_cases.test_policy_validate_refuses_a_malformed_policy(
        ReferencePolicy(), reference_calls()
    )


def policy_allow_list(capture: Capture) -> None:
    for layer in policy_cases.LAYERS:
        policy_cases.test_policy_allow_list_restricts_in_either_layer(
            ReferencePolicy(), reference_calls(), capture, layer
        )


def policy_positive_control(capture: Capture) -> None:
    policy_cases.test_policy_allows_what_no_rule_refuses(
        ReferencePolicy(), reference_calls(), capture
    )


def policy_determinism(capture: Capture) -> None:
    policy_cases.test_policy_decisions_are_deterministic(
        ReferencePolicy(), reference_calls(), capture
    )


CYCLE_CASES: tuple[Callable[[Capture], None], ...] = (
    resolution,
    bounded_failure,
    policy_validate,
    policy_deny,
    policy_allow_list,
    policy_positive_control,
    policy_protected,
    policy_determinism,
    policy_failure,
)


def run_cycle() -> None:
    for case in CYCLE_CASES:
        run_case(case)


def snapshot() -> tracemalloc.Snapshot:
    gc.collect()
    return tracemalloc.take_snapshot()


def recurring_growth(before: tracemalloc.Snapshot, after: tracemalloc.Snapshot) -> int:
    return sum(
        stat.size_diff
        for stat in after.compare_to(before, "lineno")
        if stat.count_diff >= CYCLES // 2
    )


def test_cases_release_every_resource_after_repeated_runs() -> None:
    tracemalloc.start()
    try:
        for _ in range(WARM_UP):
            run_cycle()
        threads = threading.active_count()
        descriptors = descriptor_count()
        before = snapshot()

        for _ in range(CYCLES):
            run_cycle()
        retained = recurring_growth(before, snapshot())
    finally:
        tracemalloc.stop()

    assert threading.active_count() == threads
    assert descriptor_count() == descriptors
    assert current_diagnostics() is None
    assert retained < MAX_RETAINED_BYTES


def test_a_stuck_capture_is_bounded_and_reported() -> None:
    result = run_python(["-I", str(PROBE)], {})

    assert result.code == 0, result.stderr
    assert json.loads(result.stdout) == {
        "timed_out": True,
        "refused": True,
        "held": 1,
    }
