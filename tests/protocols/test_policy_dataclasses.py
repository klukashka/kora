from dataclasses import replace
from typing import Final

import pytest

from kora.protocols.harness import Enforcement, HarnessValueRefused
from kora.protocols.policy import PolicyDecision, PolicyValueRefused, ToolCallRequest
from kora.protocols.vcs import WorkspaceRef
from tests.protocols.harness_documents import REFUSED_MARKER, WORKSPACE_REF

WORKSPACE: Final[WorkspaceRef] = WorkspaceRef.model_validate(WORKSPACE_REF)
REQUEST: Final[ToolCallRequest] = ToolCallRequest(
    harness="claude",
    tool="Bash",
    tool_use_id="call-1",
    agent_id=None,
    arguments={"command": REFUSED_MARKER},
    workspace=WORKSPACE,
    node_path="graph.node",
)
DECISION: Final[PolicyDecision] = PolicyDecision(allow=True, reason="allowed")
TERMINATION: Final[Enforcement] = Enforcement(action="terminate", cause="stall")
REFUSED: Final[
    list[tuple[ToolCallRequest | PolicyDecision | Enforcement, str, object]]
] = [
    (REQUEST, "harness", f"{REFUSED_MARKER} harness"),
    (REQUEST, "tool", ""),
    (REQUEST, "tool_use_id", 7),
    (REQUEST, "agent_id", "a" * 257),
    (REQUEST, "workspace", WORKSPACE_REF),
    (REQUEST, "node_path", f"{REFUSED_MARKER} path"),
    (REQUEST, "arguments", {"command": float("nan")}),
    (REQUEST, "arguments", [REFUSED_MARKER]),
    (DECISION, "allow", 1),
    (DECISION, "reason", f"{REFUSED_MARKER} because"),
    (DECISION, "reason", "namespace.deny.12345"),
    (DECISION, "reason", "graph.deny.0"),
    (TERMINATION, "cause", None),
    (Enforcement(action="continue", cause=None), "cause", "stall"),
]


def test_request_repr_shows_no_argument() -> None:
    built = REQUEST

    rendered = repr(built)

    assert REFUSED_MARKER not in rendered
    assert "call-1" in rendered


def test_request_owns_a_parsed_copy_of_its_arguments() -> None:
    arguments: dict[str, object] = {"command": "ls"}

    built = replace(REQUEST, **{"arguments": arguments})
    arguments["command"] = REFUSED_MARKER

    assert built.arguments == {"command": "ls"}


@pytest.mark.parametrize(
    ("allow", "reason"),
    [(True, "allowed"), (False, "protected_path"), (False, "node.allow.0")],
)
def test_policy_decision_accepts_reasons_and_rule_ids(allow: bool, reason: str) -> None:
    built = PolicyDecision(allow=allow, reason=reason)

    assert (built.allow, built.reason) == (allow, reason)


@pytest.mark.parametrize(("valid", "field", "value"), REFUSED)
def test_policy_dataclasses_check_their_fields(
    valid: ToolCallRequest | PolicyDecision | Enforcement, field: str, value: object
) -> None:
    changes = {field: value}

    with pytest.raises((PolicyValueRefused, HarnessValueRefused)) as raised:
        replace(valid, **changes)

    assert str(raised.value).endswith(f" {field} is invalid")
    assert REFUSED_MARKER not in str(raised.value)
