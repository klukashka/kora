import ast
from pathlib import Path

ROOT: Path = Path(__file__).parents[2]
SOURCE: Path = ROOT / "src"
NAMES: frozenset[str] = frozenset(
    {"reveal", "_kora_secret", "_SecretValue__kora_secret"}
)
CALLS: frozenset[str] = frozenset(
    {"getattr", "attrgetter", "methodcaller", "getattr_static", "__getattribute__"}
)
ALLOWED: tuple[str, ...] = (
    "src/kora/runner/",
    "src/kora/executor/",
    "src/kora/plugins/runtime/",
    "src/kora/plugins/harness/",
    "src/kora/plugins/vcs/",
    "src/kora/plugins/secrets/",
    "src/kora/protocols/secret_value.py",
    "src/kora/conformance/secrets.py",
)


def called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def names_a_secret(node: ast.Call) -> bool:
    return any(
        isinstance(argument, ast.Constant) and argument.value in NAMES
        for argument in node.args
    )


def violations(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in NAMES:
            lines.append(node.lineno)
        if (
            isinstance(node, ast.Call)
            and called_name(node) in CALLS
            and names_a_secret(node)
        ):
            lines.append(node.lineno)
    return lines


def check(text: str) -> list[int]:
    return violations(ast.parse(text))


def test_secret_reveal_is_confined() -> None:
    offenders = {
        relative: lines
        for path in SOURCE.rglob("*.py")
        if not (relative := str(path.relative_to(ROOT))).startswith(ALLOWED)
        and (lines := check(path.read_text()))
    }

    assert offenders == {}


def test_the_check_flags_every_access_form() -> None:
    sources = [
        "value.reveal()",
        "value._kora_secret",
        "value._SecretValue__kora_secret",
        'getattr(value, "reveal")',
        'operator.attrgetter("_kora_secret")(value)',
        'operator.methodcaller("reveal")(value)',
        'inspect.getattr_static(value, "_kora_secret")',
        'value.__getattribute__("reveal")',
    ]

    flagged = [check(source) for source in sources]

    assert all(flagged)
