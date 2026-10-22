import kora.protocols.common
import kora.protocols.identity
import kora.protocols.secret_value
import kora.protocols.secrets

INVENTORY: dict[object, tuple[str, ...]] = {
    kora.protocols.secret_value: (
        "SecretValue",
        "SecretScope",
        "SecretAdmission",
        "SecretTransfer",
    ),
    kora.protocols.common: (
        "ProtocolContext",
        "SecretResolver",
        "ProviderProtocol",
        "BoundaryModel",
    ),
    kora.protocols.identity: (
        "ProcessIdentity",
        "ProcessIdentitySource",
        "GrantBinding",
    ),
    kora.protocols.secrets: ("SecretsProvider", "SecretsCapabilities"),
}


def test_protocol_inventory() -> None:
    missing = [
        f"{module}.{name}"
        for module, names in INVENTORY.items()
        for name in names
        if not hasattr(module, name)
    ]

    assert missing == []
