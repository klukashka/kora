import asyncio
import dataclasses
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final, cast

import pytest

from kora.conformance.capture import Capture
from kora.conformance.common import (
    MARKER,
    check,
    context_for,
    kora_capture,
    test_capabilities_are_frozen_and_consistent,
    test_config_schema_is_an_object_schema,
    test_failure_is_a_bounded_protocol_error,
    test_provider_logs_through_its_context,
)
from kora.ir import ContractViolation, ToolPolicy
from kora.ir.json import JsonObject, JsonValue
from kora.protocols.policy import (
    Policy,
    PolicyDecision,
    PolicyLayers,
    ToolCallRequest,
    is_reason,
)

__all__ = [
    "kora_capture",
    "kora_exercise",
    "kora_provider",
    "test_capabilities_are_frozen_and_consistent",
    "test_config_schema_is_an_object_schema",
    "test_failure_is_a_bounded_protocol_error",
    "test_policy_allow_list_restricts_in_either_layer",
    "test_policy_allows_what_no_rule_refuses",
    "test_policy_decisions_are_deterministic",
    "test_policy_deny_in_either_layer_denies",
    "test_policy_protected_path_is_denied",
    "test_policy_validate_refuses_a_malformed_policy",
    "test_provider_logs_through_its_context",
]

PROTECTED: Final[tuple[str, ...]] = ("tests/**",)
EDIT_TOOLS: Final[dict[str, frozenset[str]]] = {
    "claude": frozenset({"Edit", "Write", "NotebookEdit"}),
    "codex": frozenset({"apply_patch"}),
}
MARKER_ARGUMENT: Final[str] = "kora_conformance_marker"
LAYERS: Final[tuple[str, str]] = ("namespace", "node")
CODE_MARKER: Final[str] = MARKER.replace("-", "_")


@dataclass(frozen=True)
class EditCall:
    tool: str
    pattern: str
    build: Callable[[str], ToolCallRequest]


@dataclass(frozen=True)
class PolicyCalls:
    shell: ToolCallRequest
    shell_other: ToolCallRequest
    matching: str
    other: str
    edits: tuple[EditCall, ...]
    malformed: JsonObject


@pytest.fixture
def kora_provider(kora_policy_provider: Policy) -> Policy:
    return kora_policy_provider


@pytest.fixture
def kora_exercise(kora_policy_exercise: object) -> object:
    return kora_policy_exercise


def marked(request: ToolCallRequest) -> ToolCallRequest:
    arguments = {**request.arguments, MARKER_ARGUMENT: CODE_MARKER}
    return dataclasses.replace(request, arguments=arguments)


def variants(request: ToolCallRequest) -> Iterator[ToolCallRequest]:
    for agent_id in (None, "conformance-agent"):
        for tool_use_id in ("conformance-call-1", "conformance-call-2"):
            yield marked(
                dataclasses.replace(request, agent_id=agent_id, tool_use_id=tool_use_id)
            )


def policy(allow: tuple[str, ...] = (), deny: tuple[str, ...] = ()) -> ToolPolicy:
    return ToolPolicy.model_validate({"allow": list(allow), "deny": list(deny)})


def layered(layer: str, chosen: ToolPolicy, other: ToolPolicy) -> PolicyLayers:
    if layer == "namespace":
        return PolicyLayers(namespace=chosen, node=other)
    return PolicyLayers(namespace=other, node=chosen)


def exact_decision(value: object) -> PolicyDecision | None:
    return value if type(value) is PolicyDecision else None


def violations_of(value: object) -> list[ContractViolation] | None:
    if not isinstance(value, list):
        return None
    items = cast("list[object]", value)
    found = [item for item in items if isinstance(item, ContractViolation)]
    return found if len(found) == len(items) else None


def decided(
    provider: Policy,
    capture: Capture,
    request: ToolCallRequest,
    layers: PolicyLayers,
    protected: tuple[str, ...] = (),
) -> PolicyDecision:
    decision = exact_decision(
        asyncio.run(provider.decide(request, layers, protected, context_for(capture)))
    )
    check(decision is not None, "decide returns a PolicyDecision")
    if decision is None:
        raise TypeError
    check(is_reason(decision.reason), "the reason is a rule id or a policy reason")
    check(CODE_MARKER not in decision.reason, "the reason echoes nothing")
    return decision


def texts(value: JsonValue) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from texts(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from texts(item)


@pytest.mark.kora_conformance
def test_policy_validate_refuses_a_malformed_policy(
    kora_policy_provider: Policy, kora_policy_calls: PolicyCalls
) -> None:
    document = kora_policy_calls.malformed

    violations = violations_of(kora_policy_provider.validate(document))

    check(violations is not None, "validate returns ContractViolations")
    found = violations or []
    check(bool(found), "validate refuses the malformed policy")
    for violation in found:
        check(violation.path is not None, "each violation has a path")
        rendered = json.dumps(violation.model_dump(mode="json"))
        check(
            all(text not in rendered for text in texts(document) if len(text) > 3),
            "no violation carries the policy's text",
        )


@pytest.mark.kora_conformance
@pytest.mark.parametrize("layer", LAYERS)
def test_policy_deny_in_either_layer_denies(
    kora_policy_provider: Policy,
    kora_policy_calls: PolicyCalls,
    kora_capture: Capture,
    layer: str,
) -> None:
    calls = kora_policy_calls
    grid = [
        (same, other)
        for same in ((), (calls.matching,))
        for other in ((), (calls.matching,))
    ]

    decisions = [
        decided(
            kora_policy_provider,
            kora_capture,
            request,
            layered(
                layer,
                policy(allow=same, deny=(calls.other, calls.matching)),
                policy(allow=other),
            ),
        )
        for same, other in grid
        for request in variants(calls.shell)
    ]

    check(all(not decision.allow for decision in decisions), "a matching deny denies")


@pytest.mark.kora_conformance
@pytest.mark.parametrize("layer", LAYERS)
def test_policy_allow_list_restricts_in_either_layer(
    kora_policy_provider: Policy,
    kora_policy_calls: PolicyCalls,
    kora_capture: Capture,
    layer: str,
) -> None:
    calls = kora_policy_calls

    decisions = [
        decided(
            kora_policy_provider,
            kora_capture,
            request,
            layered(layer, policy(allow=(calls.other,)), policy(allow=other)),
        )
        for other in ((), (calls.matching,))
        for request in variants(calls.shell)
    ]

    check(
        all(not decision.allow for decision in decisions),
        "an allow list that does not match denies",
    )


@pytest.mark.kora_conformance
def test_policy_allows_what_no_rule_refuses(
    kora_policy_provider: Policy, kora_policy_calls: PolicyCalls, kora_capture: Capture
) -> None:
    calls = kora_policy_calls
    layers = (
        PolicyLayers(namespace=policy(), node=policy()),
        PolicyLayers(
            namespace=policy(allow=(calls.matching,)),
            node=policy(allow=(calls.matching,)),
        ),
        PolicyLayers(
            namespace=policy(deny=(calls.other,)), node=policy(deny=(calls.other,))
        ),
    )

    decisions = [
        decided(kora_policy_provider, kora_capture, request, chosen)
        for chosen in layers
        for request in variants(calls.shell)
    ]

    check(
        all(decision.allow for decision in decisions),
        "a call no rule refuses is allowed",
    )


def protected_variants(edit: EditCall) -> Iterator[ToolCallRequest]:
    root = edit.build("tests/unit/case.py").workspace.root.rstrip("/")
    for path in (
        "tests/unit/case.py",
        f"{root}/tests/unit/case.py",
        "./tests/unit/case.py",
        "src/../tests/unit/case.py",
    ):
        yield marked(edit.build(path))


@pytest.mark.kora_conformance
def test_policy_protected_path_is_denied(
    kora_policy_provider: Policy, kora_policy_calls: PolicyCalls, kora_capture: Capture
) -> None:
    calls = kora_policy_calls
    harnesses = {edit.build("tests/unit/case.py").harness for edit in calls.edits}
    named = {edit.tool for edit in calls.edits}

    decisions = [
        decided(
            kora_policy_provider,
            kora_capture,
            request,
            PolicyLayers(namespace=namespace, node=node),
            PROTECTED,
        )
        for edit in calls.edits
        for namespace, node in (
            (policy(), policy()),
            (policy(deny=(calls.other,)), policy()),
            (policy(allow=(edit.pattern,)), policy(allow=(edit.pattern,))),
        )
        for request in protected_variants(edit)
    ]

    check(
        all(EDIT_TOOLS.get(harness, frozenset()) <= named for harness in harnesses),
        "every file-edit tool of each named harness is covered",
    )
    check(
        all(not decision.allow for decision in decisions), "a protected path is denied"
    )


@pytest.mark.kora_conformance
def test_policy_decisions_are_deterministic(
    kora_policy_provider: Policy, kora_policy_calls: PolicyCalls, kora_capture: Capture
) -> None:
    calls = kora_policy_calls
    layers = PolicyLayers(namespace=policy(deny=(calls.matching,)), node=policy())
    requests = [marked(calls.shell), marked(calls.shell_other)] * 3

    decisions = [
        decided(kora_policy_provider, kora_capture, request, layers)
        for request in requests
    ]

    check(
        [decision.allow for decision in decisions] == [False, True] * 3,
        "interleaved requests keep their own decisions",
    )
    check(
        len({decision.reason for decision in decisions[0::2]}) == 1
        and len({decision.reason for decision in decisions[1::2]}) == 1,
        "identical requests give identical reasons",
    )
