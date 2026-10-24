import os
from collections.abc import Callable
from typing import Final

from kora.conformance.common import CONFORMANCE_COMPONENT
from kora.conformance.policy import EditCall, PolicyCalls
from kora.ir import ContractViolation, ToolPolicy
from kora.ir.json import JsonObject
from kora.observability import CATALOG, EventSpec, FieldKind, Level
from kora.protocols.common import ProtocolContext
from kora.protocols.errors import KoraProtocolError
from kora.protocols.policy import (
    PolicyCapabilities,
    PolicyDecision,
    PolicyLayers,
    ToolCallRequest,
)
from kora.protocols.vcs import WorkspaceRef

ROOT: Final[str] = "/workspace/attempt"
WORKSPACE: Final[WorkspaceRef] = WorkspaceRef(
    attempt_id="attempt-1",
    root=ROOT,
    repo="example/repo",
    commit="c0ffee",
    base_commit="c0ffee",
    branch=None,
)
DECIDE_FAILED: Final[EventSpec] = CATALOG.register(
    EventSpec(
        "conformance.test_policy.decide_failed",
        CONFORMANCE_COMPONENT,
        Level.WARNING,
        "a test policy could not decide a call",
        {"reason": FieldKind.STRING},
    )
)
EDIT_TOOLS: Final[dict[str, str]] = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}


def shell(command: str) -> ToolCallRequest:
    return ToolCallRequest(
        harness="claude",
        tool="Bash",
        tool_use_id="call-1",
        agent_id=None,
        arguments={"command": command},
        workspace=WORKSPACE,
        node_path="node",
    )


def edit_builder(tool: str) -> Callable[[str], ToolCallRequest]:
    def build(path: str) -> ToolCallRequest:
        return ToolCallRequest(
            harness="claude",
            tool=tool,
            tool_use_id="call-1",
            agent_id=None,
            arguments={EDIT_TOOLS[tool]: path},
            workspace=WORKSPACE,
            node_path="node",
        )

    return build


def reference_calls() -> PolicyCalls:
    return PolicyCalls(
        shell=shell("git status"),
        shell_other=shell("ls"),
        matching="Bash(git)",
        other="Bash(rm)",
        edits=tuple(
            EditCall(tool=tool, pattern=tool, build=edit_builder(tool))
            for tool in EDIT_TOOLS
        ),
        malformed={"allow": "Bash(git push)"},
    )


def argument(request: ToolCallRequest) -> str:
    key = EDIT_TOOLS.get(request.tool, "command")
    value = request.arguments.get(key)
    return value if isinstance(value, str) else ""


def matches(pattern: str, request: ToolCallRequest) -> bool:
    tool, _, rest = pattern.partition("(")
    if tool != request.tool:
        return False
    return not rest or argument(request).startswith(rest.removesuffix(")"))


def relative_path(request: ToolCallRequest) -> str:
    path = argument(request)
    if path.startswith(request.workspace.root.rstrip("/") + "/"):
        path = path[len(request.workspace.root.rstrip("/")) + 1 :]
    return os.path.normpath(path)


def protected(request: ToolCallRequest, paths: tuple[str, ...]) -> bool:
    if request.tool not in EDIT_TOOLS:
        return False
    path = relative_path(request)
    prefixes = [entry.removesuffix("/**") for entry in paths]
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def layer_items(layers: PolicyLayers) -> tuple[tuple[str, ToolPolicy], ...]:
    return (("namespace", layers.namespace), ("node", layers.node))


def first_deny(layers: PolicyLayers, request: ToolCallRequest) -> str | None:
    for name, layer in layer_items(layers):
        for index, pattern in enumerate(layer.deny):
            if matches(pattern, request):
                return f"{name}.deny.{index}"
    return None


def allow_refusal(layers: PolicyLayers, request: ToolCallRequest) -> bool:
    return any(
        layer.allow and not any(matches(pattern, request) for pattern in layer.allow)
        for _, layer in layer_items(layers)
    )


class ReferencePolicy:
    name: str = "reference"
    config_schema: JsonObject = {"type": "object"}

    def capabilities(self) -> PolicyCapabilities:
        return PolicyCapabilities()

    def validate(self, policy: JsonObject) -> list[ContractViolation]:
        return [
            ContractViolation(
                code="tool_policy_invalid",
                message="the tool policy is invalid",
                path=(key,),
            )
            for key, value in policy.items()
            if not isinstance(value, list)
        ]

    async def decide(
        self,
        request: ToolCallRequest,
        layers: PolicyLayers,
        protected_paths: tuple[str, ...],
        ctx: ProtocolContext,
    ) -> PolicyDecision:
        if protected(request, protected_paths):
            return PolicyDecision(allow=False, reason="protected_path")
        denied = first_deny(layers, request)
        if denied is not None:
            return PolicyDecision(allow=False, reason=denied)
        if allow_refusal(layers, request):
            return PolicyDecision(allow=False, reason="not_allowed")
        return PolicyDecision(allow=True, reason="allowed")


class NodeAllowIgnoringPolicy(ReferencePolicy):
    async def decide(
        self,
        request: ToolCallRequest,
        layers: PolicyLayers,
        protected_paths: tuple[str, ...],
        ctx: ProtocolContext,
    ) -> PolicyDecision:
        widened = PolicyLayers(
            namespace=layers.namespace,
            node=ToolPolicy.model_validate({"deny": list(layers.node.deny)}),
        )
        return await super().decide(request, widened, protected_paths, ctx)


class AnyDenyPolicy(ReferencePolicy):
    async def decide(
        self,
        request: ToolCallRequest,
        layers: PolicyLayers,
        protected_paths: tuple[str, ...],
        ctx: ProtocolContext,
    ) -> PolicyDecision:
        if layers.namespace.deny or layers.node.deny:
            return PolicyDecision(allow=False, reason="not_allowed")
        return await super().decide(request, layers, protected_paths, ctx)


class FirstDenyOnlyPolicy(ReferencePolicy):
    async def decide(
        self,
        request: ToolCallRequest,
        layers: PolicyLayers,
        protected_paths: tuple[str, ...],
        ctx: ProtocolContext,
    ) -> PolicyDecision:
        trimmed = PolicyLayers(
            namespace=ToolPolicy.model_validate(
                {
                    "allow": list(layers.namespace.allow),
                    "deny": list(layers.namespace.deny[:1]),
                }
            ),
            node=ToolPolicy.model_validate(
                {"allow": list(layers.node.allow), "deny": list(layers.node.deny[:1])}
            ),
        )
        return await super().decide(request, trimmed, protected_paths, ctx)


class SubagentSkippingPolicy(ReferencePolicy):
    async def decide(
        self,
        request: ToolCallRequest,
        layers: PolicyLayers,
        protected_paths: tuple[str, ...],
        ctx: ProtocolContext,
    ) -> PolicyDecision:
        if request.agent_id is not None:
            return PolicyDecision(allow=True, reason="allowed")
        return await super().decide(request, layers, protected_paths, ctx)


class AllowOverridesDenyPolicy(ReferencePolicy):
    async def decide(
        self,
        request: ToolCallRequest,
        layers: PolicyLayers,
        protected_paths: tuple[str, ...],
        ctx: ProtocolContext,
    ) -> PolicyDecision:
        for _, layer in layer_items(layers):
            if any(matches(pattern, request) for pattern in layer.allow):
                return PolicyDecision(allow=True, reason="allowed")
        return await super().decide(request, layers, protected_paths, ctx)


class ToolCachingPolicy(ReferencePolicy):
    def __init__(self) -> None:
        self.cache: dict[str, PolicyDecision] = {}

    async def decide(
        self,
        request: ToolCallRequest,
        layers: PolicyLayers,
        protected_paths: tuple[str, ...],
        ctx: ProtocolContext,
    ) -> PolicyDecision:
        cached = self.cache.get(request.tool)
        if cached is None:
            cached = await super().decide(request, layers, protected_paths, ctx)
            self.cache[request.tool] = cached
        return cached


class ExactProtectedPathPolicy(ReferencePolicy):
    async def decide(
        self,
        request: ToolCallRequest,
        layers: PolicyLayers,
        protected_paths: tuple[str, ...],
        ctx: ProtocolContext,
    ) -> PolicyDecision:
        literal = argument(request) in protected_paths
        if request.tool in EDIT_TOOLS and literal:
            return PolicyDecision(allow=False, reason="protected_path")
        return await super().decide(request, layers, (), ctx)


class SilentValidatePolicy(ReferencePolicy):
    def validate(self, policy: JsonObject) -> list[ContractViolation]:
        return []


class LooseDecisionPolicy(ReferencePolicy):
    async def decide(
        self,
        request: ToolCallRequest,
        layers: PolicyLayers,
        protected_paths: tuple[str, ...],
        ctx: ProtocolContext,
    ) -> PolicyDecision:
        return LooseDecision(allow=True, reason="allowed")


class LooseDecision(PolicyDecision):
    pass


async def policy_exercise(ctx: ProtocolContext, marker: str) -> object:
    ctx.logger.emit(DECIDE_FAILED, reason="unreachable")
    raise KoraProtocolError("policy_failed", "the policy failed", False, {})


async def echoing_policy_exercise(ctx: ProtocolContext, marker: str) -> object:
    raise KoraProtocolError(
        "policy_failed", f"the policy failed on {marker}", False, {}
    )
