import hashlib
import sys
from pathlib import Path

from kora.observability import ProcessDiagnostics
from kora.plugins.runtime.local.agent import AgentSources
from kora.plugins.runtime.local.installation import InstallationConfiguration
from kora.plugins.runtime.local.launch import ExpectedBinary
from kora.plugins.runtime.local.production import ClaudeResolver
from kora.plugins.runtime.local.supervisor import run
from kora.testing.runner import AllowDenyPolicies


class AvailableCredentials:
    def available(self) -> bool:
        return True


def sources(
    diagnostics: ProcessDiagnostics, installation: InstallationConfiguration
) -> AgentSources:
    target = Path(sys.argv[1])
    binary = ExpectedBinary(
        target, hashlib.sha256(target.read_bytes()).hexdigest(), target.stat().st_size
    )
    resolver = ClaudeResolver(
        {
            "credentials": {"ANTHROPIC_API_KEY": "secret://env/ANTHROPIC_API_KEY"},
            "max_retries": 0,
        },
        diagnostics,
        AvailableCredentials(),
    )
    return AgentSources(resolver, AllowDenyPolicies(), binary=binary)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[2:], sources))
