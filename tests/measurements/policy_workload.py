import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from kora.conformance.capture import Capture
from kora.conformance.common import context_for
from kora.ir import ToolPolicy
from kora.ir.globs import Fuel, FuelExhausted
from kora.plugins.policy.allow_deny import (
    AllowDenyPolicy,
    Prepared,
    evaluated,
    prepared,
)
from kora.plugins.policy.allow_deny.arguments import MAX_PATCH_BYTES, ArgumentInvalid
from kora.plugins.policy.allow_deny.patterns import MAX_ARRAY_BYTES, MAX_PATTERNS
from kora.plugins.policy.allow_deny.shell import MAX_COMMAND_CHARACTERS
from kora.protocols.policy import PolicyLayers, ToolCallRequest
from kora.protocols.vcs import WorkspaceRef

COMMAND_CHARACTERS: Final[int] = MAX_COMMAND_CHARACTERS
PATCH_BYTES: Final[int] = MAX_PATCH_BYTES
DEFAULT_FIND_WINDOW: Final[int] = 2400
DEEP_SEGMENTS: Final[int] = 50
LINK_CHAIN: Final[int] = 40


@dataclass(frozen=True)
class PolicyCall:
    request: ToolCallRequest
    layers: PolicyLayers
    protected: tuple[str, ...]


@dataclass(frozen=True)
class Spend:
    units: int
    exhausted: bool


def workspace(root: Path) -> WorkspaceRef:
    return WorkspaceRef(
        attempt_id="attempt-1",
        root=str(root),
        repo="example/repo",
        commit="c0ffee",
        base_commit="c0ffee",
        branch=None,
    )


def call(root: Path, harness: str, tool: str, command: str) -> ToolCallRequest:
    return ToolCallRequest(
        harness=harness,
        tool=tool,
        tool_use_id="call-1",
        agent_id=None,
        arguments={"command": command},
        workspace=workspace(root),
        node_path="graph.node",
    )


def filled(pattern: Callable[[int], str]) -> tuple[str, ...]:
    patterns: list[str] = []
    size = 0
    while len(patterns) < MAX_PATTERNS:
        text = pattern(len(patterns))
        size += len(text.encode("utf-8"))
        if size > MAX_ARRAY_BYTES:
            break
        patterns.append(text)
    return tuple(patterns)


def tool_policy(allow: tuple[str, ...] = (), deny: tuple[str, ...] = ()) -> ToolPolicy:
    return ToolPolicy.model_validate({"allow": list(allow), "deny": list(deny)})


def both(allow: tuple[str, ...], deny: tuple[str, ...]) -> PolicyLayers:
    return PolicyLayers(
        namespace=tool_policy(allow, deny), node=tool_policy(allow, deny)
    )


def patch(headers: list[str]) -> str:
    return "\n".join(["*** Begin Patch", *headers, "*** End Patch"])


def patch_of(paths: Callable[[int], str]) -> str:
    headers: list[str] = []
    size = len(patch([]))
    while True:
        header = f"*** Add File: {paths(len(headers))}"
        size += len(header.encode("utf-8")) + 1
        if size > PATCH_BYTES:
            return patch(headers)
        headers.append(header)


def realistic_shell(root: Path) -> PolicyCall:
    command = "git log " + " ".join(f"arg{index}" for index in range(500))
    return PolicyCall(
        call(root, "codex", "Bash", command[:4096]),
        both(
            tuple(f"Bash(tool{index} *)" for index in range(99)) + ("Bash(git *)",),
            tuple(f"Bash(danger{index} *)" for index in range(100)),
        ),
        tuple(f"gate{index}" for index in range(10)),
    )


def realistic_patch(root: Path) -> PolicyCall:
    text = patch([f"*** Add File: src/module{index}.py" for index in range(64)])
    return PolicyCall(
        call(root, "codex", "apply_patch", text),
        both(
            ("Edit(src/**)",), tuple(f"Edit(vendor{index}/**)" for index in range(100))
        ),
        tuple(f"gate{index}" for index in range(10)),
    )


def shell_allows(pattern: str) -> tuple[str, ...]:
    return filled(lambda index: pattern)


def short_shell_denies(root: Path) -> PolicyCall:
    return PolicyCall(
        call(root, "codex", "Bash", " ".join(["a"] * (COMMAND_CHARACTERS // 2))),
        both(shell_allows("Bash(a*c*a)"), filled(lambda index: "Bash(a*b*a)")),
        (),
    )


def long_shell_denies(root: Path) -> PolicyCall:
    middle = "a" * 92 + "baaa"
    return PolicyCall(
        call(root, "codex", "Bash", "a" * COMMAND_CHARACTERS),
        both(
            shell_allows(f"Bash(a*{middle}*a)"),
            filled(lambda index: f"Bash(a*{middle}*a)"),
        ),
        (),
    )


def short_needles(root: Path) -> PolicyCall:
    needle = "a" * 92 + "baaa"
    return PolicyCall(
        call(root, "codex", "Bash", "a" * DEFAULT_FIND_WINDOW),
        both(
            shell_allows(f"Bash(a*{needle}*a)"),
            filled(lambda index: f"Bash(a*{needle}*a)"),
        ),
        (),
    )


def edit_paths(root: Path) -> PolicyCall:
    text = patch_of(lambda index: f"src/{'a' * 48}{index}.py")
    star_dense = "Edit(" + "*a" * 30 + "*z)"
    return PolicyCall(
        call(root, "codex", "apply_patch", text),
        both(
            filled(lambda index: "Edit(docs/*)" if index else "Edit(src/*)"),
            filled(lambda index: star_dense),
        ),
        (),
    )


def many_short_paths(root: Path) -> PolicyCall:
    return PolicyCall(
        call(root, "codex", "apply_patch", patch_of(lambda index: f"p{index}")),
        both((), ()),
        ("docs",),
    )


def protected_globs(root: Path) -> PolicyCall:
    segments = "/".join(["a"] * 200)
    glob = "**/" + "/".join(["*a*a*"] * 30) + "/b"
    return PolicyCall(
        call(
            root, "codex", "apply_patch", patch_of(lambda index: f"{segments}/f{index}")
        ),
        both((), ()),
        tuple([glob] * 64),
    )


def deep_root(root: Path) -> str:
    deep = root
    for _ in range(DEEP_SEGMENTS):
        deep = deep / "dddddddddddddddd"
    deep.mkdir(parents=True, exist_ok=True)
    previous = root / "hop0"
    previous.symlink_to(deep)
    for index in range(1, LINK_CHAIN):
        current = root / f"hop{index}"
        current.symlink_to(previous)
        previous = current
    return previous.name


def deep_resolution(root: Path) -> PolicyCall:
    entry = deep_root(root)
    return PolicyCall(
        call(root, "codex", "apply_patch", patch_of(lambda index: f"{entry}/f{index}")),
        both((), ()),
        ("docs",),
    )


def shell_allow_needles(root: Path) -> PolicyCall:
    needle = "a" * 92 + "baaa"
    return PolicyCall(
        call(root, "codex", "Bash", "a" * COMMAND_CHARACTERS),
        both(shell_allows(f"Bash(a*{needle}*a)"), filled(lambda index: "Bash(z*)")),
        (),
    )


def non_ascii_paths(root: Path) -> PolicyCall:
    return PolicyCall(
        call(
            root,
            "codex",
            "apply_patch",
            patch_of(lambda index: "/".join(["\u0390" * 100] * 3) + str(index)),
        ),
        both((), ()),
        ("docs",),
    )


def short_needle_paths(root: Path) -> PolicyCall:
    needle = "a" * 92 + "baaa"
    pattern = f"Edit(x*{needle}*a)"
    return PolicyCall(
        call(
            root,
            "codex",
            "apply_patch",
            patch_of(lambda index: "x/" + "/".join(["a" * 199] * 12) + str(index)),
        ),
        both(filled(lambda index: pattern), filled(lambda index: pattern)),
        (),
    )


def composite(root: Path) -> PolicyCall:
    return composite_variant(root, 0)


def composite_variant(root: Path, variant: int) -> PolicyCall:
    root.mkdir(parents=True, exist_ok=True)
    star_dense = f"Edit(v{variant}" + "*a" * 30 + "*z)"
    glob = "/".join(["*a" * 60 + "*"] * 32)[:4096].rstrip("/")
    return PolicyCall(
        call(
            root,
            "codex",
            "apply_patch",
            patch_of(lambda index: f"src/{'a' * 48}{index}.py"),
        ),
        both(filled(lambda index: star_dense), filled(lambda index: star_dense)),
        tuple([glob] * 64),
    )


ROWS: Final[dict[str, Callable[[Path], PolicyCall]]] = {
    "policy_shell_short_denies": short_shell_denies,
    "policy_shell_long_denies": long_shell_denies,
    "policy_short_needles": short_needles,
    "policy_edit_paths": edit_paths,
    "policy_many_short_paths": many_short_paths,
    "policy_protected_globs": protected_globs,
    "policy_deep_resolution": deep_resolution,
    "policy_non_ascii_paths": non_ascii_paths,
    "policy_short_needle_paths": short_needle_paths,
    "policy_composite": composite,
    "policy_shell_allow_needles": shell_allow_needles,
}
FLOOR_SHARE: Final[float] = 1 / 3
REALISTIC: Final[dict[str, Callable[[Path], PolicyCall]]] = {
    "policy_realistic_shell": realistic_shell,
    "policy_realistic_patch": realistic_patch,
}


def ready(subject: PolicyCall) -> Prepared:
    result = prepared(subject.request, subject.layers, subject.protected)
    if isinstance(result, str):
        raise LookupError(result)
    return result


def spend(subject: PolicyCall, budget: int) -> Spend:
    fuel = Fuel(budget)
    exhausted = False
    try:
        evaluated(subject.request, ready(subject), fuel)
    except FuelExhausted:
        exhausted = True
    except ArgumentInvalid:
        exhausted = False
    return Spend(fuel.spent, exhausted)


def decide(subject: PolicyCall, capture: Capture, provider: AllowDenyPolicy) -> bool:
    outcome = asyncio.run(
        provider.decide(
            subject.request, subject.layers, subject.protected, context_for(capture)
        )
    )
    return outcome.allow
