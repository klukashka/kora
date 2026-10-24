import asyncio
import dataclasses
import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Final, cast

import pytest

from kora.conformance.capture import Capture
from kora.conformance.common import (
    MARKER,
    check,
    context_for,
    fail,
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
    is_allowing_reason,
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

PROTECTED_SETS: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("tests/**",), "tests/unit/case.py"),
    (("config/*.lock",), "config/kora.lock"),
)
UNPROTECTED_PATH: Final[str] = "src/case.py"
EDIT_TOOLS: Final[dict[str, frozenset[str]]] = {
    "claude": frozenset({"Edit", "Write", "NotebookEdit"}),
    "codex": frozenset({"apply_patch"}),
}
MARKER_ARGUMENT: Final[str] = "kora_conformance_marker"
LAYERS: Final[tuple[str, str]] = ("namespace", "node")
CODE_MARKER: Final[str] = MARKER.replace("-", "_")
Decision = tuple[ToolCallRequest, PolicyLayers, tuple[str, ...]]


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


def violations_of(value: object) -> list[ContractViolation] | None:
    if not isinstance(value, list):
        return None
    items = cast("list[object]", value)
    found = [item for item in items if isinstance(item, ContractViolation)]
    return found if len(found) == len(items) else None


def checked(value: object) -> PolicyDecision:
    if type(value) is not PolicyDecision:
        fail("decide returns a PolicyDecision")
    check(type(value.allow) is bool, "allow is an exact bool")
    check(is_reason(value.reason), "the reason is a rule id or a policy reason")
    check(
        is_allowing_reason(value.reason) == value.allow,
        "the reason agrees with the decision",
    )
    check(CODE_MARKER not in value.reason, "the reason echoes nothing")
    return value


def decided(
    provider: Policy, capture: Capture, calls: Iterable[Decision]
) -> list[PolicyDecision]:
    context = context_for(capture)
    with asyncio.Runner() as runner:
        return [
            checked(runner.run(provider.decide(request, layers, protected, context)))
            for request, layers, protected in calls
        ]


def edit_requests(
    calls: PolicyCalls, path: str
) -> Iterator[tuple[EditCall, ToolCallRequest]]:
    for edit in calls.edits:
        for request in variants(edit.build(path)):
            yield edit, request


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


def rule_targets(calls: PolicyCalls) -> Iterator[tuple[ToolCallRequest, str]]:
    for request in variants(calls.shell):
        yield request, calls.matching
    for edit, request in edit_requests(calls, UNPROTECTED_PATH):
        yield request, edit.pattern


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
        (
            request,
            layered(
                layer,
                policy(allow=same, deny=(calls.other, matching)),
                policy(allow=other),
            ),
            (),
        )
        for request, matching in rule_targets(calls)
        for same in ((), (matching,))
        for other in ((), (matching,))
    ]

    decisions = decided(kora_policy_provider, kora_capture, grid)

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
    grid = [
        (request, layered(layer, policy(allow=(calls.other,)), policy(allow=other)), ())
        for request, matching in rule_targets(calls)
        for other in ((), (matching,))
    ]

    decisions = decided(kora_policy_provider, kora_capture, grid)

    check(
        all(not decision.allow for decision in decisions),
        "an allow list that does not match denies",
    )


@pytest.mark.kora_conformance
def test_policy_allows_what_no_rule_refuses(
    kora_policy_provider: Policy, kora_policy_calls: PolicyCalls, kora_capture: Capture
) -> None:
    calls = kora_policy_calls
    grid = [
        (request, chosen, protected)
        for request, matching in rule_targets(calls)
        for chosen in (
            PolicyLayers(namespace=policy(), node=policy()),
            PolicyLayers(
                namespace=policy(allow=(matching,)), node=policy(allow=(matching,))
            ),
            PolicyLayers(
                namespace=policy(deny=(calls.other,)), node=policy(deny=(calls.other,))
            ),
        )
        for protected, _ in PROTECTED_SETS
    ]

    decisions = decided(kora_policy_provider, kora_capture, grid)

    check(
        all(decision.allow for decision in decisions),
        "a call no rule refuses is allowed",
    )


def path_forms(root: str, path: str) -> tuple[str, ...]:
    trimmed = root.rstrip("/")
    name = trimmed.rsplit("/", 1)[-1]
    return (
        path,
        f"{trimmed}/{path}",
        f"./{path}",
        f"src/../{path}",
        f"{trimmed}//{path}",
        f"{trimmed}/../{name}/{path}",
        f"../{name}/{path}",
    )


def protected_requests(edit: EditCall, path: str) -> Iterator[ToolCallRequest]:
    root = edit.build(path).workspace.root
    for form in path_forms(root, path):
        yield from variants(edit.build(form))


@pytest.mark.kora_conformance
def test_policy_protected_path_is_denied(
    kora_policy_provider: Policy, kora_policy_calls: PolicyCalls, kora_capture: Capture
) -> None:
    calls = kora_policy_calls
    built = [(edit, edit.build(UNPROTECTED_PATH)) for edit in calls.edits]
    harnesses = {request.harness for _, request in built}
    covered = {request.tool for _, request in built}
    grid = [
        (request, PolicyLayers(namespace=namespace, node=node), protected)
        for edit in calls.edits
        for protected, path in PROTECTED_SETS
        for namespace, node in (
            (policy(), policy()),
            (policy(deny=(calls.other,)), policy()),
            (policy(allow=(edit.pattern,)), policy(allow=(edit.pattern,))),
        )
        for request in protected_requests(edit, path)
    ]

    check(bool(calls.edits), "the adopter supplies at least one file-edit call")
    check(
        all(request.tool == edit.tool for edit, request in built),
        "each edit builder makes a call of its own tool",
    )
    check(
        all(harness in EDIT_TOOLS for harness in harnesses),
        "every named harness is in the coverage matrix",
    )
    check(
        all(
            EDIT_TOOLS[harness] <= covered
            for harness in harnesses
            if harness in EDIT_TOOLS
        ),
        "every file-edit tool of each named harness is covered",
    )
    decisions = decided(kora_policy_provider, kora_capture, grid)
    check(
        all(not decision.allow for decision in decisions), "a protected path is denied"
    )


@pytest.mark.kora_conformance
def test_policy_decisions_are_deterministic(
    kora_policy_provider: Policy, kora_policy_calls: PolicyCalls, kora_capture: Capture
) -> None:
    calls = kora_policy_calls
    layers = PolicyLayers(namespace=policy(deny=(calls.matching,)), node=policy())
    pairs = list(zip(variants(calls.shell), variants(calls.shell_other), strict=True))
    grid = [(request, layers, ()) for pair in pairs * 3 for request in pair]

    decisions = decided(kora_policy_provider, kora_capture, grid)

    check(
        [decision.allow for decision in decisions] == [False, True] * len(pairs) * 3,
        "interleaved requests keep their own decisions",
    )
    check(
        len({decision.reason for decision in decisions[0::2]}) == 1
        and len({decision.reason for decision in decisions[1::2]}) == 1,
        "identical requests give identical reasons",
    )
