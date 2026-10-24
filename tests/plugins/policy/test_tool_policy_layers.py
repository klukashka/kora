from pathlib import Path
from typing import Final

import pytest

from kora.conformance.capture import Capture
from kora.conformance.common import kora_capture
from tests.plugins.policy.policy_support import (
    debug_capture,
    decision,
    edit,
    layers,
    policy,
    prepared_root,
    reason,
    refusal,
    request,
    shell,
)

__all__ = ["kora_capture"]

INDIRECTION: Final[list[str]] = [
    "ls a#;rm -rf x",
    "ls; rm x",
    "echo x >> f",
    "cat <<< x",
    "a 2>&1",
    "a &> f",
    "a |& b",
    "echo $HOME",
    "echo `id`",
    'echo "$x"',
    "a\nb",
    "a\tb",
    "/bin/r? -rf x",
    "/bin/r[m] -rf x",
    "! rm x",
    "coproc rm x",
    "trap 'rm -rf x' EXIT",
    "=rm x",
    "noglob rm x",
    "uv run rm x",
    "FOO=1 rm x",
    "FOO+=1 bash x.sh",
    "ssh host 'r\"m\" -rf x'",
    "tmux new 'rm -rf x'",
    "\\rm x",
    "'rm' x",
    "/bin/sh x",
    "perl -e 1",
    "python3.12 x.py",
    "bash -lc 'x'",
    "BASH x.sh",
    "find . -exec rm {} ;",
    "git commit -c x",
    "x --exec=y",
    "apply_patch x",
    "echo 'unterminated",
    "x" * 16_385,
    "",
]
SHELL_PATCHES: Final[list[str]] = [
    "cd x && apply_patch <<'EOF'\n*** Begin Patch\n*** End Patch\nEOF",
    "APPLY_PATCH <<'EOF'",
    "echo applypatch",
    "x" * 16_385,
    "echo 'unterminated",
]


@pytest.mark.parametrize("command", INDIRECTION, ids=range(len(INDIRECTION)))
def test_tool_policy_layers_indirection_matches_every_deny(
    kora_capture: Capture, tmp_path: Path, command: str
) -> None:
    call = shell(tmp_path, command)

    denied = reason(kora_capture, call, layers(node=policy(deny=("Bash(zz-none)",))))
    allowed = [
        decision(kora_capture, call, layers(node=policy(allow=(allow,)))).allow
        for allow in ("Bash", "*", "Bash(*)", "B*")
    ]

    assert denied == "node.deny.0"
    assert allowed == [False] * 4


@pytest.mark.parametrize("command", SHELL_PATCHES, ids=range(len(SHELL_PATCHES)))
def test_tool_policy_layers_refuses_shell_patches_under_the_shell_condition(
    kora_capture: Capture, tmp_path: Path, command: str
) -> None:
    claude = shell(tmp_path, command)
    codex = shell(tmp_path, command, harness="codex")

    protected = reason(kora_capture, claude, protected=("tests",))
    edit_rule = reason(
        kora_capture, codex, layers(node=policy(deny=("apply_patch(src/*)",)))
    )
    unguarded = reason(kora_capture, claude)

    assert (protected, edit_rule, unguarded) == (
        "argument_invalid",
        "argument_invalid",
        "allowed",
    )


def test_tool_policy_layers_reason_precedence(
    kora_capture: Capture, tmp_path: Path
) -> None:
    root = prepared_root(tmp_path)
    outside = edit(root, "Edit", "/etc/hosts")
    protected_edit = edit(root, "Edit", "tests/unit/case.py")
    git = shell(root, "git status")

    reasons = [
        reason(kora_capture, outside, protected=("**",)),
        reason(
            kora_capture,
            protected_edit,
            layers(node=policy(deny=("Edit",))),
            ("tests",),
        ),
        reason(
            kora_capture,
            git,
            layers(policy(deny=("Bash(git *)",)), policy(deny=("Bash(git *)",))),
        ),
        reason(
            kora_capture,
            git,
            layers(node=policy(deny=("Bash(zz)", "Bash(git *)", "Bash(git*)"))),
        ),
        reason(
            kora_capture,
            git,
            layers(policy(allow=("Read",)), policy(deny=("Bash(git *)",))),
        ),
        reason(kora_capture, git, layers(policy(allow=("Read",)), policy())),
        reason(kora_capture, git, layers(policy(), policy(allow=("Read",)))),
        reason(kora_capture, git, layers(policy(allow=("Bash(git *)",)), policy())),
    ]

    assert reasons == [
        "argument_invalid",
        "protected_path",
        "namespace.deny.0",
        "node.deny.1",
        "node.deny.0",
        "not_allowed",
        "not_allowed",
        "allowed",
    ]


def test_tool_policy_layers_raise_in_order(
    kora_capture: Capture, tmp_path: Path
) -> None:
    oversized = policy(allow=tuple(f"Read{index}" for index in range(1001)))
    overflowing = policy(allow=("(",) * 1000, deny=("(",) * 1000)
    invalid_layer = layers(node=policy(allow=("Bash(",)))
    git = shell(tmp_path, "git status")
    unknown = request(tmp_path, "unknown-harness", "Bash", {"command": "ls"})

    codes = [
        refusal(kora_capture, unknown, invalid_layer, ("/abs",)),
        refusal(kora_capture, git, invalid_layer, ("/abs",)),
        refusal(kora_capture, git, layers(node=oversized)),
        refusal(kora_capture, git, layers(node=overflowing)),
        refusal(kora_capture, git, protected=("/abs",)),
        refusal(kora_capture, git, protected=tuple(f"d{index}" for index in range(65))),
    ]

    assert codes == [
        "policy_harness_unsupported",
        "tool_policy_invalid",
        "tool_policy_invalid",
        "tool_policy_invalid",
        "protected_paths_invalid",
        "protected_paths_invalid",
    ]


def test_tool_policy_layers_log_one_content_free_record_per_outcome(
    tmp_path: Path,
) -> None:
    marker = "kora-marker-in-the-command"
    git = shell(tmp_path, f"git {marker}")
    denying = layers(node=policy(deny=("Bash(git *)",)))
    unknown = request(tmp_path, "unknown-harness", "Bash", {"command": marker})

    with debug_capture() as capture:
        decision(capture, git)
        decision(capture, git, denying)
        refusal(capture, unknown)
        refusal(capture, git, protected=("/abs",))
        records = capture.finish()

    events = [
        (
            record.get("event"),
            record.get("reason"),
            record.get("cause"),
            record.get("code"),
        )
        for record in records
        if str(record.get("event", "")).startswith("policy.allow_deny.")
    ]
    assert events == [
        ("policy.allow_deny.allowed", "allowed", None, None),
        ("policy.allow_deny.denied", "node.deny.0", "rule", None),
        ("policy.allow_deny.refused", None, None, "policy_harness_unsupported"),
        ("policy.allow_deny.refused", None, None, "protected_paths_invalid"),
    ]
    assert all(marker not in str(record) for record in records)
