from claude_agent_sdk import __version__
from claude_agent_sdk._cli_version import __cli_version__
from packaging.specifiers import SpecifierSet

from kora.ir.json import JsonObject
from kora.plugins.harness.claude.adapter import ClaudeHarness
from kora.plugins.harness.claude.configuration import CLI_VERSION, SDK_VERSION


def test_claude_policy_coverage_matches_section_12() -> None:
    harness = ClaudeHarness()

    capabilities = harness.capabilities()

    assert capabilities.policy_coverage == {
        "shell",
        "file_edit",
        "mcp_tool",
        "subagent",
        "hosted_tool",
    }
    assert not capabilities.resume
    assert capabilities.reports_usage_per_turn
    assert harness.name == "claude"
    assert harness.egress_hosts == ("api.anthropic.com",)


def test_claude_installed_version_matches_pinned_sdk() -> None:
    harness = ClaudeHarness()
    tested = SpecifierSet(harness.tested_versions)

    installed = harness.installed_version()

    assert installed == __cli_version__ == CLI_VERSION
    assert __version__ == SDK_VERSION
    assert installed in tested
    assert str(tested) == f"=={CLI_VERSION}"
    assert "2.1.300" not in tested
    assert "2.1.302" not in tested


def test_claude_check_config_warns_only_for_hooks() -> None:
    harness = ClaudeHarness()
    supported: JsonObject = {
        "prompt": "task",
        "model": "claude-sonnet-4-5",
        "system_prompt_append": "instruction",
        "skills": [],
        "subagents": [],
        "mcp_servers": {},
        "tool_policy": {},
        "permission_mode": "dont_ask",
        "sandbox": "workspace_write",
        "trust_repo_config": False,
        "network_policy": {},
        "output_schema": {},
        "env": {},
        "secret_env": {},
    }

    accepted = harness.check_config(supported)
    warned = harness.check_config(
        {**supported, "hooks": {"private-hook": "private-content"}}
    )
    empty = harness.check_config({"hooks": {}})

    assert accepted == []
    assert len(warned) == 1
    assert warned[0].code == "IR008"
    assert warned[0].path == ("hooks",)
    assert "private-" not in warned[0].message
    assert empty == warned
