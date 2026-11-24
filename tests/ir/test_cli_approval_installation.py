import pytest

from kora.ir.deployment_checks import INSTALLATION_HINT, check_deployment_document
from kora.ir.document import GraphDocument
from kora.ir.json import JsonObject
from tests.api.deployment_cases import DEFAULT_POLICY, SELECTION, skeleton_ir
from tests.engine.graph_output_cases import child

APPROVAL_PATH = ("graph", "nodes", "approve")


@pytest.mark.parametrize(
    ("changes", "expected_path"),
    [
        ({"inputs": {}}, ("inputs",)),
        (
            {
                "inputs": {
                    "gate": {
                        "type": "CommandResult",
                        "from": "gate",
                        "output": "result",
                    },
                    "other": {
                        "type": "CommandResult",
                        "from": "gate",
                        "output": "result",
                    },
                }
            },
            ("inputs",),
        ),
        ({"input": {"from": "agent"}}, ("inputs", "gate")),
        ({"input": {"from": "missing"}}, ("inputs", "gate")),
        ({"input": {"type": "StructuredOutput"}}, ("inputs", "gate")),
        ({"input": {"type": "VcsRef"}}, ("inputs", "gate")),
        ({"input": {"output": "other"}}, ("inputs", "gate")),
        ({"input": {"path": "exit_code"}}, ("inputs", "gate")),
        ({"input": {"optional": True}}, ("inputs", "gate")),
        ({"config": {"context": "inputs.gate.exit_code"}}, ("config", "context")),
        ({"config": {"context": "params.gate"}}, ("config", "context")),
        ({"config": {"context": "inputs.other"}}, ("config", "context")),
        ({"config": {"context": "inputs[0]"}}, ("config", "context")),
        ({"config": {"channel": "slack"}}, ("config", "channel")),
        ({"config": {"binds": {}}}, ("config", "binds")),
        ({}, None),
        ({"config": {"context": "inputs.gate"}}, None),
        ({"config": {"context": "inputs['gate']"}}, None),
        ({"input": {"optional": False}}, None),
    ],
    ids=[
        "empty-inputs",
        "second-input",
        "agent-source",
        "missing-source",
        "structured-output",
        "other-type",
        "other-output",
        "input-path",
        "optional",
        "context-suffix",
        "other-root",
        "other-input",
        "numeric-index",
        "channel",
        "binds",
        "omitted-context",
        "member",
        "string-index",
        "required-input",
    ],
)
def test_cli_approval_rechecks_exact_authored_context(
    changes: JsonObject, expected_path: tuple[str, ...] | None
) -> None:
    ir = skeleton_ir()
    approval = child(ir, "graph", "nodes", "approve")
    approval["depends_on"] = ["gate"]
    if "inputs" in changes:
        approval["inputs"] = changes["inputs"]
    if "input" in changes:
        child(approval, "inputs", "gate").update(child(changes, "input"))
    if "config" in changes:
        child(approval, "config").update(child(changes, "config"))
    document = GraphDocument.model_validate(ir)
    authored = document.model_dump()

    result = check_deployment_document(document, SELECTION, DEFAULT_POLICY)

    findings = [error for error in result.diagnostics.errors if error.code == "IR028"]
    assert [(error.code, error.path) for error in findings] == (
        [] if expected_path is None else [("IR028", APPROVAL_PATH + expected_path)]
    )
    assert all(error.hint == INSTALLATION_HINT for error in findings)
    assert document.model_dump() == authored
    if expected_path is None:
        assert result.deployable


def test_cli_approval_requires_an_input_declaration() -> None:
    ir = skeleton_ir()
    approval = child(ir, "graph", "nodes", "approve")
    del approval["inputs"]
    approval["depends_on"] = ["gate"]
    document = GraphDocument.model_validate(ir)

    result = check_deployment_document(document, SELECTION, DEFAULT_POLICY)

    assert [
        (error.code, error.path)
        for error in result.diagnostics.errors
        if error.code == "IR028"
    ] == [("IR028", APPROVAL_PATH + ("inputs",))]


def test_cli_approval_preserves_malformed_context_diagnostics() -> None:
    ir = skeleton_ir()
    child(ir, "graph", "nodes", "approve", "config")["context"] = "inputs["
    document = GraphDocument.model_validate(ir)

    result = check_deployment_document(document, SELECTION, DEFAULT_POLICY)

    assert [(error.code, error.path) for error in result.diagnostics.errors] == [
        ("IR011", APPROVAL_PATH + ("config", "context"))
    ]
