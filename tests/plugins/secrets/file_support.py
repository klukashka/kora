import os
from pathlib import Path

from kora.conformance.common import NAMESPACE_ID
from kora.ir.json import JsonObject
from kora.plugins.secrets.file import FileSecretsConfiguration, FileSecretsProvider

VALUE: str = 'file-secret-"quoted"-é-7c21'


def private_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o700)
    return directory


def write_secret(directory: Path, name: str, value: bytes) -> Path:
    path = directory / name
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def configuration(directory: Path) -> JsonObject:
    return {
        "directory": str(directory),
        "namespace_prefixes": {NAMESPACE_ID: ["CONF_"], "other": ["OTHER_"]},
    }


def provider(directory: Path) -> FileSecretsProvider:
    return FileSecretsProvider(
        FileSecretsConfiguration.model_validate(configuration(directory))
    )


def open_secret_descriptors(paths: tuple[Path, ...]) -> set[int]:
    identities = {(path.stat().st_dev, path.stat().st_ino) for path in paths}
    descriptors: set[int] = set()
    for name in os.listdir("/dev/fd"):
        try:
            metadata = os.fstat(int(name))
        except OSError:
            continue
        if (metadata.st_dev, metadata.st_ino) in identities:
            descriptors.add(int(name))
    return descriptors
