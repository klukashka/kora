import pytest

from kora.api.graph_deployment import PreparedDeployment
from kora.api.graph_deployment_limits import DEPLOYMENT, IR_BYTES
from kora.api.graph_execution_preparation import (
    PreparedGraphDeploymentInputs,
    prepare_graph_deployment_inputs,
)
from kora.cel_eval.thread import Evaluator
from kora.db.connection import Database
from kora.db.graph_value_json import TextBudget, render_object
from kora.engine.start_facts import InstallationRefusal
from kora.ir.diagnostic_collection import DiagnosticCollection
from kora.ir.json import JsonObject
from tests.api.deployment_cases import (
    DEFAULT_POLICY,
    SELECTORS,
    evaluator,
    principal,
    publish,
    seeded_namespace,
    skeleton_ir,
)
from tests.engine.graph_output_cases import child
from tests.engine.start_cases import DeployedGraph, graph_id, start, start_rows
from tests.support.output_bindings import publication_binding
from tests.support.parameter_bindings import parameter_binding

__all__ = ["evaluator"]


def section(changes: JsonObject, name: str) -> JsonObject:
    return child(changes, name) if name in changes else {}


async def publish_legacy_approval(
    database: Database, evaluator: Evaluator, changes: JsonObject
) -> DeployedGraph:
    ir = skeleton_ir()
    approval = child(ir, "graph", "nodes", "approve")
    child(approval, "config").update(section(changes, "config"))
    child(approval, "inputs", "gate").update(section(changes, "input"))
    child(approval, "inputs").update(section(changes, "inputs"))
    inputs = await prepare_graph_deployment_inputs(
        render_object(ir, TextBudget(IR_BYTES)),
        (),
        evaluator,
        binding=publication_binding(),
        parameter_binding=parameter_binding(),
        selectors=SELECTORS,
        storage_identity=None,
        limits=DEPLOYMENT,
    )
    assert isinstance(inputs, PreparedGraphDeploymentInputs)
    namespace_id, token_id = await seeded_namespace(database)
    published = await publish(
        database,
        PreparedDeployment(
            "skeleton",
            inputs,
            inputs.logical.digests.ir_digest,
            DiagnosticCollection((), (), 0, 0),
            DEFAULT_POLICY,
        ),
        namespace_id,
        token_id,
    )
    return DeployedGraph(
        namespace_id,
        await graph_id(database, namespace_id),
        published.graph_version_id,
        principal(namespace_id, token_id),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"config": {"context": "inputs.gate.exit_code"}}, ("config", "context")),
        ({"config": {"channel": "slack"}}, ("config", "channel")),
        ({"config": {"binds": {}}}, ("config", "binds")),
        ({"input": {"optional": True}}, ("inputs", "gate")),
        ({"input": {"path": "exit_code"}}, ("inputs", "gate")),
        (
            {
                "inputs": {
                    "other": {
                        "type": "CommandResult",
                        "from": "gate",
                        "output": "result",
                    }
                }
            },
            ("inputs",),
        ),
    ],
    ids=["context", "channel", "binds", "optional", "path", "another-input"],
)
async def test_cli_approval_rechecks_exact_authored_context(
    database: Database,
    evaluator: Evaluator,
    changes: JsonObject,
    field: tuple[str, ...],
) -> None:
    case = await publish_legacy_approval(database, evaluator, changes)
    before = await start_rows(database)

    result = await start(database, case, version=case.version_id)
    after = await start_rows(database)

    assert isinstance(result, InstallationRefusal)
    assert [(error.code, error.path) for error in result.diagnostics.errors] == [
        ("IR028", ("graph", "nodes", "approve", *field))
    ]
    assert after == before
    assert all(not rows for rows in after.values())
