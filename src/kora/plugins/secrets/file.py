import asyncio
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from pydantic import StrictStr, field_validator
from pydantic_core import PydanticCustomError

from kora.ir.json import JsonObject, parse_json_object
from kora.observability import CATALOG, EventSpec, FieldKind, Level
from kora.plugins.secrets.common import (
    NAME,
    NAME_BODY,
    Name,
    admitted,
    readable,
    refused,
)
from kora.protocols.common import BoundaryModel, ProtocolContext
from kora.protocols.identity import Identifier
from kora.protocols.metadata import ProtocolName, ProviderMetadata
from kora.protocols.secret_value import SecretValue
from kora.protocols.secrets import SecretsCapabilities

REFERENCE: Final[re.Pattern[str]] = re.compile(f"secret://file/({NAME_BODY})")
MAX_SECRET_BYTES: Final[int] = 4096
MAX_LISTED_NAMES: Final[int] = 1000
VERSION: Final[str] = "1"
REFUSALS: Final[dict[str, str]] = {
    "secret_reference_invalid": "the secret reference is invalid",
    "secret_reference_not_allowed": "the secret reference is not allowed",
    "secret_resolution_unsealed": "secrets resolve only after the request is sealed",
    "secret_admission_missing": "the call has no secret admission",
    "secret_missing": "the secret is not set",
    "secret_unreadable": "the secret cannot be read",
    "secret_admission_refused": "the secret was refused admission",
}
COMPONENT: Final[str] = CATALOG.register_component("secrets.file")
RESOLVED: Final[EventSpec] = CATALOG.register(
    EventSpec(
        "secrets.file.resolved",
        COMPONENT,
        Level.INFO,
        "a secret reference was resolved and admitted",
        {},
    )
)
REFUSED: Final[EventSpec] = CATALOG.register(
    EventSpec(
        "secrets.file.refused",
        COMPONENT,
        Level.WARNING,
        "a secret reference was refused",
        {"reason": FieldKind.STRING},
    )
)


class FileSecretsConfiguration(BoundaryModel):
    directory: StrictStr
    namespace_prefixes: dict[Identifier, tuple[Name, ...]]

    @field_validator("directory")
    @classmethod
    def require_absolute_directory(cls, value: str) -> str:
        if not os.path.isabs(value) or not encodable_path(value):
            raise PydanticCustomError(
                "secret_directory_invalid",
                "the directory must be an absolute, encodable path",
            )
        return value


def encodable_path(value: str) -> bool:
    try:
        os.fsencode(value)
    except UnicodeEncodeError:
        return False
    return "\0" not in value


CONFIG_SCHEMA: Final[JsonObject] = parse_json_object(
    FileSecretsConfiguration.model_json_schema()
)


@dataclass(frozen=True)
class ReadRefusal:
    code: str


def open_directory(directory: str) -> int:
    directory = directory.rstrip("/") or "/"
    while directory.endswith("/."):
        directory = directory[:-2].rstrip("/") or "/"
    descriptor = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    accepted = False
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise OSError("the secret directory is not private")
        accepted = True
        return descriptor
    finally:
        if not accepted:
            os.close(descriptor)


def read_file(directory: str, name: str) -> str | ReadRefusal:
    try:
        directory_fd = open_directory(directory)
        try:
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return ReadRefusal("secret_missing")
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or metadata.st_mode & 0o077
                    or metadata.st_nlink != 1
                ):
                    return ReadRefusal("secret_unreadable")
                data = bytearray()
                while len(data) <= MAX_SECRET_BYTES:
                    chunk = os.read(descriptor, MAX_SECRET_BYTES + 1 - len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                if len(data) > MAX_SECRET_BYTES:
                    return ReadRefusal("secret_unreadable")
                return data.decode("utf-8").removesuffix("\n")
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)
    except (OSError, UnicodeDecodeError):
        return ReadRefusal("secret_unreadable")


def listed_names(directory: str, prefix: str) -> list[str]:
    try:
        descriptor = open_directory(directory)
        try:
            names = os.listdir(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        return []
    return sorted(
        name
        for name in names
        if name.startswith(prefix) and NAME.fullmatch(name) is not None
    )[:MAX_LISTED_NAMES]


class FileSecretsProvider:
    name: str = "file"
    config_schema: JsonObject = CONFIG_SCHEMA

    def __init__(self, configuration: FileSecretsConfiguration) -> None:
        self._directory = configuration.directory
        self._prefixes: Mapping[str, tuple[str, ...]] = MappingProxyType(
            dict(configuration.namespace_prefixes)
        )
        self._listed = tuple(
            sorted({prefix for group in self._prefixes.values() for prefix in group})
        )

    def capabilities(self) -> SecretsCapabilities:
        return SecretsCapabilities()

    async def resolve(self, reference: str, ctx: ProtocolContext) -> SecretValue:
        match = REFERENCE.fullmatch(reference)
        if match is None:
            refused(ctx, "secret_reference_invalid", REFUSED, REFUSALS)
        name = match[1]
        if not readable(self._prefixes, ctx.namespace_id, name):
            refused(ctx, "secret_reference_not_allowed", REFUSED, REFUSALS)
        if ctx.request_digest is None:
            refused(ctx, "secret_resolution_unsealed", REFUSED, REFUSALS)
        admission = ctx.secret_admission
        if admission is None:
            refused(ctx, "secret_admission_missing", REFUSED, REFUSALS)
        value = await asyncio.to_thread(read_file, self._directory, name)
        if isinstance(value, ReadRefusal):
            refused(ctx, value.code, REFUSED, REFUSALS)
        secret = admitted(admission, value)
        if secret is None:
            refused(ctx, "secret_admission_refused", REFUSED, REFUSALS)
        ctx.logger.emit(RESOLVED)
        return secret

    async def list_names(self, prefix: str) -> list[str]:
        if not any(prefix.startswith(listed) for listed in self._listed):
            return []
        return await asyncio.to_thread(listed_names, self._directory, prefix)


def create_provider(configuration: JsonObject) -> FileSecretsProvider:
    return FileSecretsProvider(FileSecretsConfiguration.model_validate(configuration))


def provider_metadata() -> ProviderMetadata:
    return ProviderMetadata(
        ProtocolName.SECRETS, "file", create_provider, CONFIG_SCHEMA, VERSION
    )
