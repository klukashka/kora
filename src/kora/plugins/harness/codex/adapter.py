from typing import ClassVar

from kora.ir import ConfigWarning
from kora.ir.json import JsonObject
from kora.plugins.harness.codex.deployment import CONFIG_SCHEMA
from kora.plugins.harness.codex.launch_configuration import CODEX_VERSION
from kora.plugins.harness.codex.reservations import RESERVED_ENV
from kora.protocols.harness import HarnessCapabilities


class CodexHarness:
    name: ClassVar[str] = "codex"
    config_schema: ClassVar[JsonObject] = CONFIG_SCHEMA
    egress_hosts: ClassVar[tuple[str, ...]] = ("api.openai.com",)
    tested_versions: ClassVar[str] = f"=={CODEX_VERSION}"
    reserved_env: ClassVar[tuple[str, ...]] = RESERVED_ENV

    def capabilities(self) -> HarnessCapabilities:
        return HarnessCapabilities(
            resume=False,
            reports_usage_per_turn=True,
            policy_coverage=frozenset({"shell", "file_edit"}),
        )

    def check_config(self, config: JsonObject) -> list[ConfigWarning]:
        if "subagents" not in config:
            return []
        return [
            ConfigWarning(
                code="IR008",
                message="the Codex harness does not support node subagents",
                path=("subagents",),
            )
        ]
