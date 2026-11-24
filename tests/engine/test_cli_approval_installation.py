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
from kora.engine.start_acceptance import AcceptanceOutcome, StartAcceptance
from kora.engine.start_facts import InstallationRefusal
from kora.ir.agent_secrets import UNENFORCED
from kora.ir.deployment_checks import DeploymentSelection
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
from tests.db.seed import Seeder
from tests.engine.graph_output_cases import child
from tests.engine.start_cases import DeployedGraph, deploy, start, start_rows
from tests.support.output_bindings import publication_binding
from tests.support.parameter_bindings import parameter_binding

__all__ = ["evaluator"]


async def publish_legacy_approval(
    database: Database, evaluator: Evaluator, changes: JsonObject
) -> DeployedGraph:
    ir = skeleton_ir()
    child(ir, "graph", "nodes", "approve", "config").update(changes)
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
    async with database.transaction() as writer:
        graph_id = await Seeder(writer).graph(namespace_id, "skeleton")
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
        graph_id,
        published.graph_version_id,
        principal(namespace_id, token_id),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"context": "inputs.gate.exit_code"}, "context"),
        ({"channel": "slack"}, "channel"),
        ({"binds": {}}, "binds"),
    ],
    ids=["context", "channel", "binds"],
)
async def test_cli_approval_rechecks_exact_authored_context(
    database: Database, evaluator: Evaluator, changes: JsonObject, field: str
) -> None:
    case = await publish_legacy_approval(database, evaluator, changes)
    before = await start_rows(database)

    result = await start(database, case, version=case.version_id)
    after = await start_rows(database)

    assert isinstance(result, InstallationRefusal)
    assert [(error.code, error.path) for error in result.diagnostics.errors] == [
        ("IR028", ("graph", "nodes", "approve", "config", field))
    ]
    assert after == before
    assert all(not rows for rows in after.values())


@pytest.mark.asyncio
async def test_cli_approval_replay_precedes_installation_check(
    database: Database, evaluator: Evaluator
) -> None:
    case = await deploy(database, evaluator)
    accepted = await start(database, case)
    before = await start_rows(database)
    unsupported = DeploymentSelection("uninstalled", UNENFORCED)

    refused = await start(database, case, key="fresh", selection=unsupported)
    replayed = await start(database, case, selection=unsupported)
    mismatched = await start(
        database, case, allow_unenforced_secrets=True, selection=unsupported
    )
    after = await start_rows(database)

    assert isinstance(accepted, StartAcceptance)
    assert accepted.outcome is AcceptanceOutcome.ACCEPTED
    assert isinstance(refused, InstallationRefusal)
    assert replayed == StartAcceptance(AcceptanceOutcome.REPLAYED, accepted.response)
    assert mismatched == StartAcceptance(AcceptanceOutcome.MISMATCHED)
    assert after == before
