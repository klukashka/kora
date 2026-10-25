import ast
import sys
from pathlib import Path

import pytest

PLUGINS: Path = Path(__file__).parents[3] / "src" / "kora" / "plugins" / "vcs"


def imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
            roots.add((node.module or "").split(".")[0])
    return roots


@pytest.mark.parametrize(
    "helper",
    ["git/tree_build.py", "git/decoder.py", "git/export.py", "fake/store.py"],
)
def test_tree_build_imports_only_the_standard_library(helper: str) -> None:
    roots = imported_roots(PLUGINS / helper)

    assert roots <= set(sys.stdlib_module_names)
