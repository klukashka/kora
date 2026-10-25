import importlib
from types import ModuleType

import pytest

PAIRS: tuple[tuple[str, str], ...] = (
    ("kora.conformance.vcs", "tests.plugins.vcs.test_git_conformance"),
    ("kora.conformance.vcs", "tests.plugins.vcs.test_dirvcs_conformance"),
    ("kora.conformance.policy", "tests.plugins.policy.test_allow_deny_conformance"),
    ("kora.conformance.policy", "tests.conformance.test_policy_conformance"),
    ("kora.conformance.secrets", "tests.conformance.test_secrets_conformance"),
    ("kora.conformance.harness", "tests.conformance.test_harness_conformance"),
    ("kora.conformance.common", "tests.conformance.test_vcs_conformance"),
    ("kora.conformance.common", "tests.conformance.test_runtime_conformance"),
)


def cases(module: ModuleType) -> frozenset[str]:
    return frozenset(
        name
        for name, value in vars(module).items()
        if name.startswith("test_") and callable(value)
    )


@pytest.mark.parametrize(("suite", "provider"), PAIRS, ids=[pair[1] for pair in PAIRS])
def test_conformance_modules_run_every_case(suite: str, provider: str) -> None:
    expected = cases(importlib.import_module(suite))

    collected = cases(importlib.import_module(provider))

    assert collected == expected
