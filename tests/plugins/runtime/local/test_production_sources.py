from importlib.metadata import distribution
from pathlib import Path
from typing import Final

import pytest

from kora.conformance.capture import Capture
from kora.conformance.common import kora_capture
from kora.ir.json import JsonObject
from kora.plugins.harness.claude.adapter import ClaudeHarness
from kora.plugins.harness.claude.record import BINARY_ENTRY, recorded_file
from kora.plugins.runtime.local.installation import (
    InstallationConfiguration,
    RunnerRefused,
)
from kora.plugins.runtime.local.launch import ExpectedBinary
from kora.plugins.runtime.local.production import ConfiguredPolicies, production_sources
from kora.protocols.errors import KoraProtocolError
from kora.protocols.harness import AgentExecution
from kora.protocols.vcs import WorkspaceRef
from tests.plugins.runtime.local.claude_runtime_composition import AvailableCredentials
from tests.plugins.runtime.local.test_command_workspace import FAKE_COMMIT
from tests.runner.agent_support import execution
from tests.support.observability import run_python

__all__ = ["kora_capture"]

CLAUDE: Final[JsonObject] = {
    "credentials": {"ANTHROPIC_API_KEY": "secret://env/ANTHROPIC_API_KEY"}
}


def test_production_resolver_binds_execution_and_deployment(
    tmp_path: Path, kora_capture: Capture
) -> None:
    section: JsonObject = {**CLAUDE, "max_retries": 0, "cli_telemetry": True}
    installation = InstallationConfiguration({"claude": section}, {"allow_deny": {}})
    sources = production_sources(
        kora_capture.diagnostics, installation, AvailableCredentials()
    )
    request = AgentExecution.model_validate(execution(deny=("claude:Bash",)))
    workspace = WorkspaceRef(
        attempt_id="attempt-1",
        root=str(tmp_path),
        repo="/repository",
        commit=FAKE_COMMIT,
        base_commit=FAKE_COMMIT,
        branch=None,
    )

    hosted = sources.resolver.resolve(request, workspace)
    adapter = hosted.adapter

    assert isinstance(adapter, ClaudeHarness)
    assert adapter.scope.workspace == workspace
    assert adapter.scope.node_path == request.node_path
    assert adapter.scope.layers == request.layers()
    assert adapter.scope.protected_paths == ()
    assert adapter.scope.output_format is request.output_format
    assert adapter.environment.binding == "claude"
    assert adapter.environment.bash_path == "/bin/bash"
    assert adapter.environment.max_retries == 0
    assert adapter.environment.cli_telemetry is True
    assert adapter.ctx.attempt_id == workspace.attempt_id
    assert adapter.ctx.secrets is None


def test_production_sources_use_installed_binary_record_and_refuse_credentials(
    tmp_path: Path, kora_capture: Capture
) -> None:
    installation = InstallationConfiguration({"claude": CLAUDE}, {"allow_deny": {}})
    recorded = recorded_file(distribution("claude-agent-sdk"), BINARY_ENTRY)
    workspace = WorkspaceRef(
        attempt_id="attempt-1",
        root=str(tmp_path),
        repo="/repository",
        commit=FAKE_COMMIT,
        base_commit=FAKE_COMMIT,
        branch=None,
    )
    request = AgentExecution.model_validate(execution())

    sources = production_sources(kora_capture.diagnostics, installation)
    policy = sources.policies.policy(request)
    with pytest.raises(KoraProtocolError) as refused:
        sources.resolver.resolve(request, workspace)

    assert sources.binary == ExpectedBinary(
        recorded.path, recorded.sha256, recorded.size
    )
    assert policy.name == "allow_deny"
    assert sources.policies.policy(request) is policy
    assert refused.value.code == "workload_kind_unavailable"
    assert refused.value.retryable is False


def test_production_sources_without_claude_do_not_import_sdk() -> None:
    program = """
import sys
from datetime import UTC, datetime

from kora.observability import CATALOG, configure_process, read_logging_settings
from kora.plugins.runtime.local.installation import InstallationConfiguration
from kora.plugins.runtime.local.production import production_sources
from kora.protocols.loading import LOADING_COMPONENT

diagnostics = configure_process(
    LOADING_COMPONENT,
    read_logging_settings({}, CATALOG.components()),
    "production-import-probe",
    lambda: datetime.now(UTC),
)
try:
    sources = production_sources(
        diagnostics, InstallationConfiguration({}, {"allow_deny": {}})
    )
    assert sources.binary is None
    sys.stdout.write(str("claude_agent_sdk" in sys.modules))
finally:
    assert diagnostics.shutdown(5.0)
"""

    result = run_python(["-I", "-c", program], {})

    assert result.code == 0, result.stderr
    assert result.stdout == b"False"


@pytest.mark.parametrize("sections", [{}, {"allow_deny": {}, "another": {}}])
def test_configured_policies_refuse_without_exactly_one_entry(
    sections: dict[str, JsonObject], kora_capture: Capture
) -> None:
    policies = ConfiguredPolicies(sections, kora_capture.diagnostics)
    request = AgentExecution.model_validate(execution())

    with pytest.raises(KoraProtocolError) as refused:
        policies.policy(request)

    assert refused.value.code == "workload_kind_unavailable"
    assert refused.value.retryable is False


def test_production_sources_refuse_unknown_quota_group(kora_capture: Capture) -> None:
    section: JsonObject = {
        **CLAUDE,
        "quota": {"vendor_groups": {"five_hour": "undeclared"}},
    }
    installation = InstallationConfiguration({"claude": section}, {"allow_deny": {}})

    with pytest.raises(RunnerRefused) as refused:
        production_sources(kora_capture.diagnostics, installation)

    assert refused.value.args == ("installation_invalid",)


def test_configured_policy_factory_failure_refuses_start(
    kora_capture: Capture,
) -> None:
    sections: dict[str, JsonObject] = {"allow_deny": {"unexpected": True}}

    with pytest.raises(RunnerRefused) as refused:
        ConfiguredPolicies(sections, kora_capture.diagnostics)

    assert refused.value.args == ("installation_invalid",)
