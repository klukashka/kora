import asyncio
import dataclasses

from kora.conformance.capture import Capture
from kora.conformance.common import context_for, kora_capture
from kora.observability import ProcessDiagnostics
from kora.observability.redaction import MASK, StreamMasker
from kora.plugins.secrets.env import EnvSecretsConfiguration, EnvSecretsProvider
from kora.protocols.secret_value import SecretScope, SecretValue
from tests.plugins.secrets.env_support import CONFIGURATION

__all__ = ["kora_capture"]

REFERENCE: str = "secret://env/CONF_TOKEN"
VARIABLE: str = "CONF_TOKEN"
OLD_VALUE: str = "rotation-old-value-01"
NEW_VALUE: str = "rotation-new-value-02"
CYCLES: int = 50


def env_provider(environment: dict[str, str]) -> EnvSecretsProvider:
    configuration = EnvSecretsConfiguration.model_validate(CONFIGURATION)
    return EnvSecretsProvider(configuration, environment)


def resolve_into(
    provider: EnvSecretsProvider, capture: Capture, scope: SecretScope
) -> SecretValue:
    context = dataclasses.replace(
        context_for(capture), secret_admission=scope.admission()
    )
    return asyncio.run(provider.resolve(REFERENCE, context))


def registry_state(diagnostics: ProcessDiagnostics) -> tuple[int, int]:
    return diagnostics.registry.count(), diagnostics.registry.retained_bytes()


def masked(capture: Capture, text: str) -> str:
    parts: list[str] = []
    with StreamMasker(capture.diagnostics.registry, parts.append) as masker:
        masker.feed(text.encode())
    return "".join(parts)


def test_resolutions_register_into_the_caller_scope_and_return_to_baseline(
    kora_capture: Capture,
) -> None:
    provider = env_provider({"CONF_TOKEN": "cycle-secret-0000"})
    baseline = registry_state(kora_capture.diagnostics)

    states: list[tuple[int, int]] = []
    for cycle in range(CYCLES):
        provider = env_provider({"CONF_TOKEN": f"cycle-secret-{cycle:04d}"})
        scope = SecretScope(kora_capture.diagnostics)
        resolve_into(provider, kora_capture, scope)
        states.append(registry_state(kora_capture.diagnostics))
        scope.release()

    assert all(state[0] == baseline[0] + 1 for state in states)
    assert registry_state(kora_capture.diagnostics) == baseline


def test_a_rotation_keeps_both_masks_until_the_older_scope_is_released(
    kora_capture: Capture,
) -> None:
    environment = {VARIABLE: OLD_VALUE}
    provider = env_provider(environment)
    older = SecretScope(kora_capture.diagnostics)
    resolve_into(provider, kora_capture, older)
    environment[VARIABLE] = NEW_VALUE
    newer = SecretScope(kora_capture.diagnostics)
    resolve_into(provider, kora_capture, newer)

    during = masked(kora_capture, f"{OLD_VALUE} {NEW_VALUE}")
    older.release()
    after = masked(kora_capture, f"{OLD_VALUE} {NEW_VALUE}")
    newer.release()

    assert during == f"{MASK} {MASK}"
    assert after == f"{OLD_VALUE} {MASK}"
