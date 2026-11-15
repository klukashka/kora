from typing import ClassVar

from kora.ir import ConfigWarning
from kora.ir.json import JsonObject
from kora.plugins.harness.claude.configuration import CLI_VERSION
from kora.plugins.harness.claude.deployment import CONFIG_SCHEMA
from kora.plugins.harness.claude.reservations import RESERVED_ENV
from kora.protocols.harness import HarnessCapabilities


class ClaudeHarness:
    name: ClassVar[str] = "claude"
    config_schema: ClassVar[JsonObject] = CONFIG_SCHEMA
    egress_hosts: ClassVar[tuple[str, ...]] = ("api.anthropic.com",)
    tested_versions: ClassVar[str] = f"=={CLI_VERSION}"
    reserved_env: ClassVar[tuple[str, ...]] = RESERVED_ENV

    def capabilities(self) -> HarnessCapabilities:
        return HarnessCapabilities(
            resume=False,
            reports_usage_per_turn=True,
            policy_coverage=frozenset(
                {"shell", "file_edit", "mcp_tool", "subagent", "hosted_tool"}
            ),
        )

    def check_config(self, config: JsonObject) -> list[ConfigWarning]:
        if "hooks" not in config:
            return []
        return [
            ConfigWarning(
                code="IR008",
                message="the Claude harness does not support node hooks",
                path=("hooks",),
            )
        ]

    def installed_version(self) -> str:
        from claude_agent_sdk import __version__
        from claude_agent_sdk._cli_version import __cli_version__

        from kora.plugins.harness.claude.sdk import require_native_versions

        require_native_versions(__version__, __cli_version__)
        return __cli_version__
