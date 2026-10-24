import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from kora.conformance.capture import Capture
from kora.conformance.common import context_for
from kora.ir.globs import Fuel, FuelExhausted
from kora.plugins.policy.allow_deny import (
    MAX_PROTECTED_PATHS,
    AllowDenyPolicy,
    Prepared,
    evaluated,
    prepared_configuration,
)
from kora.plugins.policy.allow_deny.arguments import MAX_PATCH_BYTES, ArgumentInvalid
from kora.plugins.policy.allow_deny.patterns import (
    MAX_ARRAY_BYTES,
    MAX_LAYER_PIECES,
    MAX_PATTERN_CHARACTERS,
    MAX_PATTERNS,
)
from kora.plugins.policy.allow_deny.shell import MAX_COMMAND_CHARACTERS
from kora.protocols.policy import PolicyLayers, ToolCallRequest
from tests.plugins.policy.policy_support import patch_text, policy, request

NEEDLE: Final[str] = "a" * 92 + "baaa"
WIDE_PIECE: Final[str] = "\u1f82b"
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
    outcome: str


def call(root: Path, harness: str, tool: str, command: str) -> ToolCallRequest:
    return request(root, harness, tool, {"command": command})


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


def both(allow: tuple[str, ...], deny: tuple[str, ...]) -> PolicyLayers:
    return PolicyLayers(namespace=policy(allow, deny), node=policy(allow, deny))


def patch_of(paths: Callable[[int], str]) -> str:
    headers: list[str] = []
    size = len(patch_text())
    while True:
        header = f"*** Add File: {paths(len(headers))}"
        size += len(header.encode("utf-8")) + 1
        if size > MAX_PATCH_BYTES:
            return patch_text(*headers)
        headers.append(header)


def realistic_shell(root: Path) -> PolicyCall:
    return realistic_command(root, MAX_COMMAND_CHARACTERS // 4)


def realistic_long_shell(root: Path) -> PolicyCall:
    return realistic_command(root, MAX_COMMAND_CHARACTERS)


def realistic_command(root: Path, length: int) -> PolicyCall:
    command = "git log " + " ".join(f"arg{index}" for index in range(length // 4))
    return PolicyCall(
        call(root, "codex", "Bash", command[:length]),
        both(
            tuple(f"Bash(tool{index} *)" for index in range(99)) + ("Bash(git *)",),
            tuple(f"Bash(danger{index} *)" for index in range(100)),
        ),
        tuple(f"gate{index}" for index in range(10)),
    )


def realistic_patch(root: Path) -> PolicyCall:
    text = patch_text(*[f"*** Add File: src/module{index}.py" for index in range(64)])
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
        call(root, "codex", "Bash", " ".join(["a"] * (MAX_COMMAND_CHARACTERS // 2))),
        both(shell_allows("Bash(a*c*a)"), filled(lambda index: "Bash(a*b*a)")),
        (),
    )


def long_shell_denies(root: Path) -> PolicyCall:
    return PolicyCall(
        call(root, "codex", "Bash", "a" * MAX_COMMAND_CHARACTERS),
        both(
            shell_allows(f"Bash(a*{NEEDLE}*a)"),
            filled(lambda index: f"Bash(a*{NEEDLE}*a)"),
        ),
        (),
    )


def short_needles(root: Path) -> PolicyCall:
    return PolicyCall(
        call(root, "codex", "Bash", "a" * DEFAULT_FIND_WINDOW),
        both(
            shell_allows(f"Bash(a*{NEEDLE}*a)"),
            filled(lambda index: f"Bash(a*{NEEDLE}*a)"),
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
    return PolicyCall(
        call(root, "codex", "Bash", "a" * MAX_COMMAND_CHARACTERS),
        both(shell_allows(f"Bash(a*{NEEDLE}*a)"), filled(lambda index: "Bash(z*)")),
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
    pattern = f"Edit(x*{NEEDLE}*a)"
    return PolicyCall(
        call(
            root,
            "codex",
            "apply_patch",
            patch_of(lambda index: f"x/{index}/" + "/".join(["a" * 199] * 12)),
        ),
        both(filled(lambda index: pattern), filled(lambda index: pattern)),
        (),
    )


def piece_dense_edit_denies(root: Path) -> PolicyCall:
    dense = "Edit(" + "*a" * ((MAX_PATTERN_CHARACTERS - 10) // 2) + "*b*)"
    denies = (dense,) * (MAX_LAYER_PIECES // (dense.count("*") + 1))
    return PolicyCall(
        call(
            root,
            "codex",
            "apply_patch",
            patch_of(lambda index: f"{index}/" + "/".join(["a" * 200] * 19)),
        ),
        both((), denies),
        (),
    )


def piece_dense_globs(root: Path) -> PolicyCall:
    segments = "/".join(["a" * 60] * 40)
    glob = "**/" + "/".join(["*a" * 29 + "*"] * 4) + "/b"
    return PolicyCall(
        call(
            root, "codex", "apply_patch", patch_of(lambda index: f"{segments}/f{index}")
        ),
        both((), ()),
        tuple([glob] * 64),
    )


def anchored_first_pieces(root: Path) -> PolicyCall:
    first = "a " * 47 + "b"
    return PolicyCall(
        call(root, "codex", "Bash", ("a " * (MAX_COMMAND_CHARACTERS // 2)).rstrip()),
        both((), filled(lambda index: f"Bash({first}*z)")),
        (),
    )


def anchor_spaces(root: Path) -> PolicyCall:
    first = " " * 91 + "b"
    command = "x '" + " " * (MAX_COMMAND_CHARACTERS - 4) + "'"
    return PolicyCall(
        call(root, "codex", "Bash", command),
        both((), filled(lambda index: f"Bash({first}  *z)")),
        (),
    )


def anchor_periodic(root: Path) -> PolicyCall:
    first = ("a " * 48)[:93] + "aa"
    return PolicyCall(
        call(root, "codex", "Bash", ("a " * (MAX_COMMAND_CHARACTERS // 2)).rstrip()),
        both((), filled(lambda index: f"Bash({first}*z)")),
        (),
    )


def composite(root: Path) -> PolicyCall:
    return composite_variant(root, 0)


def composite_variant(root: Path, variant: int) -> PolicyCall:
    root.mkdir(parents=True, exist_ok=True)
    unit = "*" + WIDE_PIECE
    wide = (
        f"Edit(v{variant}" + unit * ((MAX_PATTERN_CHARACTERS - 12) // len(unit)) + "*z)"
    )
    per_array = MAX_LAYER_PIECES // 2 // (wide.count("*") + 1)
    glob = "/".join([unit * 30 + "*"] * 4)
    return PolicyCall(
        call(
            root,
            "codex",
            "apply_patch",
            patch_of(lambda index: f"src/{'a' * 48}{index}.py"),
        ),
        both((wide,) * per_array, (wide,) * per_array),
        tuple([glob] * MAX_PROTECTED_PATHS),
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
    "policy_piece_dense_edit_denies": piece_dense_edit_denies,
    "policy_piece_dense_globs": piece_dense_globs,
    "policy_anchored_first_pieces": anchored_first_pieces,
    "policy_anchor_spaces": anchor_spaces,
    "policy_anchor_periodic": anchor_periodic,
}
DECIDED_ROWS: Final[frozenset[str]] = frozenset(
    {"policy_shell_short_denies", "policy_non_ascii_paths"}
)
FLOOR_SHARE: Final[float] = 1 / 3
FLOORED: Final[frozenset[str]] = frozenset(
    {"policy_realistic_shell", "policy_realistic_patch"}
)
REALISTIC: Final[dict[str, Callable[[Path], PolicyCall]]] = {
    "policy_realistic_shell": realistic_shell,
    "policy_realistic_long_shell": realistic_long_shell,
    "policy_realistic_patch": realistic_patch,
}


def ready(subject: PolicyCall) -> Prepared:
    result = prepared_configuration(subject.layers, subject.protected)
    if isinstance(result, str):
        raise LookupError(result)
    return result


def spend(subject: PolicyCall, budget: int) -> Spend:
    fuel = Fuel(budget)
    try:
        evaluated(subject.request, ready(subject), fuel)
    except FuelExhausted:
        return Spend(fuel.spent, True, "exhausted")
    except ArgumentInvalid:
        return Spend(fuel.spent, False, "refused")
    return Spend(fuel.spent, False, "decided")


def decide(subject: PolicyCall, capture: Capture, provider: AllowDenyPolicy) -> bool:
    outcome = asyncio.run(
        provider.decide(
            subject.request, subject.layers, subject.protected, context_for(capture)
        )
    )
    return outcome.allow
