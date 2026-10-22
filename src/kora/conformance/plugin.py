import pytest


class ConformanceFixtureMissing(LookupError):
    def __init__(self, name: str) -> None:
        super().__init__(f"override the {name} fixture with the provider under test")


def missing(name: str) -> ConformanceFixtureMissing:
    return ConformanceFixtureMissing(name)


@pytest.fixture
def kora_runtime_provider() -> object:
    raise missing("kora_runtime_provider")


@pytest.fixture
def kora_runtime_exercise() -> object:
    raise missing("kora_runtime_exercise")


@pytest.fixture
def kora_harness_provider() -> object:
    raise missing("kora_harness_provider")


@pytest.fixture
def kora_harness_exercise() -> object:
    raise missing("kora_harness_exercise")


@pytest.fixture
def kora_vcs_provider() -> object:
    raise missing("kora_vcs_provider")


@pytest.fixture
def kora_vcs_exercise() -> object:
    raise missing("kora_vcs_exercise")


@pytest.fixture
def kora_policy_provider() -> object:
    raise missing("kora_policy_provider")


@pytest.fixture
def kora_policy_exercise() -> object:
    raise missing("kora_policy_exercise")


@pytest.fixture
def kora_secrets_provider() -> object:
    raise missing("kora_secrets_provider")


@pytest.fixture
def kora_secrets_exercise() -> object:
    raise missing("kora_secrets_exercise")


@pytest.fixture
def kora_secrets_resolvable() -> object:
    raise missing("kora_secrets_resolvable")


@pytest.fixture
def kora_secrets_missing_reference() -> object:
    raise missing("kora_secrets_missing_reference")
