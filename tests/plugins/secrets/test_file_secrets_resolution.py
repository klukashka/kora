import asyncio
import dataclasses
import json
import os
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from kora.conformance.capture import Capture
from kora.conformance.common import context_for
from kora.conformance.secrets import (
    kora_capture,
    kora_exercise,
    kora_provider,
    test_capabilities_are_frozen_and_consistent,
    test_config_schema_is_an_object_schema,
    test_failure_is_a_bounded_protocol_error,
    test_provider_logs_through_its_context,
    test_secrets_forbidden_reference_is_refused_like_a_missing_one,
    test_secrets_list_names_returns_names_only,
    test_secrets_missing_reference_is_a_bounded_error,
    test_secrets_resolution_returns_a_masked_value,
    test_secrets_value_is_masked_in_a_captured_stream,
)
from kora.plugins.secrets.file import (
    REFUSALS,
    FileSecretsConfiguration,
    FileSecretsProvider,
    ReadRefusal,
    read_file,
)
from kora.protocols.common import ProtocolContext
from kora.protocols.errors import KoraProtocolError
from kora.protocols.loading import load_provider
from kora.protocols.metadata import ProtocolName
from tests.plugins.secrets.file_support import (
    VALUE,
    configuration,
    open_secret_descriptors,
    private_directory,
    provider,
    write_secret,
)

__all__ = [
    "kora_capture",
    "kora_exercise",
    "kora_provider",
    "test_capabilities_are_frozen_and_consistent",
    "test_config_schema_is_an_object_schema",
    "test_failure_is_a_bounded_protocol_error",
    "test_provider_logs_through_its_context",
    "test_secrets_forbidden_reference_is_refused_like_a_missing_one",
    "test_secrets_list_names_returns_names_only",
    "test_secrets_missing_reference_is_a_bounded_error",
    "test_secrets_resolution_returns_a_masked_value",
    "test_secrets_value_is_masked_in_a_captured_stream",
]


@pytest.fixture
def secret_directory(tmp_path: Path) -> Path:
    directory = private_directory(tmp_path)
    write_secret(directory, "CONF_TOKEN", VALUE.encode())
    return directory


@pytest.fixture
def kora_secrets_provider(secret_directory: Path) -> FileSecretsProvider:
    return provider(secret_directory)


@pytest.fixture
def kora_secrets_exercise(
    kora_secrets_provider: FileSecretsProvider,
) -> Callable[[ProtocolContext, str], Awaitable[object]]:
    async def exercise(ctx: ProtocolContext, marker: str) -> object:
        return await kora_secrets_provider.resolve("secret://file/CONF_ABSENT", ctx)

    return exercise


@pytest.fixture
def kora_secrets_resolvable() -> tuple[str, str]:
    return "secret://file/CONF_TOKEN", VALUE


@pytest.fixture
def kora_secrets_missing_reference() -> str:
    return "secret://file/CONF_ABSENT"


@pytest.fixture
def kora_secrets_forbidden_reference() -> str:
    return "secret://file/OTHER_TOKEN"


@pytest.fixture
def kora_secrets_listed_prefix() -> str:
    return "CONF_"


CASES: list[tuple[str, str]] = [
    ("invalid", "secret_reference_invalid"),
    ("forbidden", "secret_reference_not_allowed"),
    ("unsealed", "secret_resolution_unsealed"),
    ("admission_missing", "secret_admission_missing"),
    ("missing", "secret_missing"),
    ("file_symlink", "secret_unreadable"),
    ("directory_symlink", "secret_unreadable"),
    ("fifo", "secret_unreadable"),
    ("file_mode", "secret_unreadable"),
    ("directory_mode", "secret_unreadable"),
    ("hardlink", "secret_unreadable"),
    ("oversize", "secret_unreadable"),
    ("utf8", "secret_unreadable"),
    ("directory_missing", "secret_unreadable"),
    ("foreign_directory", "secret_unreadable"),
    ("not_regular", "secret_unreadable"),
    ("admission_refused", "secret_admission_refused"),
]


def prepare_refusal(directory: Path, case: str) -> Path:
    path = directory / "CONF_TOKEN"
    if case in {"missing", "fifo", "file_symlink", "not_regular"}:
        path.unlink()
    if case == "fifo":
        os.mkfifo(path, 0o600)
    elif case == "not_regular":
        path.mkdir(mode=0o700)
    elif case == "file_symlink":
        target = write_secret(directory, "CONF_TARGET", VALUE.encode())
        path.symlink_to(target)
    elif case == "directory_symlink":
        link = directory.parent / "link"
        link.symlink_to(directory, target_is_directory=True)
        return link
    elif case == "file_mode":
        path.chmod(0o640)
    elif case == "directory_mode":
        directory.chmod(0o750)
    elif case == "hardlink":
        os.link(path, directory / "CONF_LINK")
    elif case == "oversize":
        path.write_bytes(b"x" * 4097)
    elif case == "utf8":
        path.write_bytes(b"\xff")
    elif case == "admission_refused":
        path.write_bytes(b"\x01" * 1000)
    elif case == "directory_missing":
        return directory / "absent"
    elif case == "foreign_directory":
        if os.getuid() != 0:
            assert Path("/").stat().st_uid != os.getuid()
            return Path("/")
        os.chown(directory, 1, -1)
    return directory


@pytest.mark.parametrize(("case", "expected"), CASES)
def test_file_secrets_refusals_are_ordered_and_content_free(
    secret_directory: Path, kora_capture: Capture, case: str, expected: str
) -> None:
    subject = provider(prepare_refusal(secret_directory, case))
    context = context_for(kora_capture)
    reference = "secret://file/CONF_TOKEN"
    if case == "invalid":
        reference = "secret://file/../CONF_TOKEN"
    elif case == "forbidden":
        reference = "secret://file/OTHER_TOKEN"
    elif case == "unsealed":
        context = dataclasses.replace(context, request_digest=None)
    elif case == "admission_missing":
        context = dataclasses.replace(context, secret_admission=None)

    async def resolve() -> object:
        return await asyncio.wait_for(subject.resolve(reference, context), timeout=2)

    with pytest.raises(KoraProtocolError) as raised:
        asyncio.run(resolve())

    assert raised.value.code == expected
    assert raised.value.message == REFUSALS[expected]
    assert raised.value.details == {}
    assert raised.value.retryable is False
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert kora_capture.scope.registered_count() == 0


@pytest.mark.parametrize(
    ("reference", "unsealed", "expected"),
    [
        ("invalid", True, "secret_reference_invalid"),
        ("secret://file/OTHER_ABSENT", True, "secret_reference_not_allowed"),
        ("secret://file/CONF_ABSENT", True, "secret_resolution_unsealed"),
        ("secret://file/CONF_ABSENT", False, "secret_admission_missing"),
    ],
)
def test_multiple_failures_report_the_earliest_code(
    secret_directory: Path,
    kora_capture: Capture,
    reference: str,
    unsealed: bool,
    expected: str,
) -> None:
    original = context_for(kora_capture)
    context = dataclasses.replace(
        original,
        request_digest=None if unsealed else original.request_digest,
        secret_admission=None,
    )
    subject = provider(secret_directory / "absent")

    with pytest.raises(KoraProtocolError) as raised:
        asyncio.run(subject.resolve(reference, context))

    assert raised.value.code == expected


def test_file_secrets_read_live_and_strip_one_newline(
    secret_directory: Path, kora_capture: Capture
) -> None:
    subject = provider(secret_directory)
    context = context_for(kora_capture)
    path = secret_directory / "CONF_TOKEN"

    first = asyncio.run(subject.resolve("secret://file/CONF_TOKEN", context)).reveal()
    path.write_bytes(b"changed-value\n")
    second = asyncio.run(subject.resolve("secret://file/CONF_TOKEN", context)).reveal()
    path.write_bytes(b"changed-value\n\n")
    third = asyncio.run(subject.resolve("secret://file/CONF_TOKEN", context)).reveal()
    path.write_bytes(b"x" * 4096)
    boundary = asyncio.run(
        subject.resolve("secret://file/CONF_TOKEN", context)
    ).reveal()
    path.write_bytes(b"v\n")
    single = read_file(str(secret_directory), "CONF_TOKEN")
    path.write_bytes(b"v\n\n")
    double = read_file(str(secret_directory), "CONF_TOKEN")

    assert first == VALUE
    assert second == "changed-value"
    assert third == "changed-value\n"
    assert boundary == "x" * 4096
    assert single == "v"
    assert double == "v\n"


def test_file_secrets_events_carry_no_value(
    secret_directory: Path, kora_capture: Capture
) -> None:
    subject = provider(secret_directory)
    context = context_for(kora_capture)

    asyncio.run(subject.resolve("secret://file/CONF_TOKEN", context))
    with pytest.raises(KoraProtocolError):
        asyncio.run(subject.resolve("secret://file/CONF_ABSENT", context))
    records = kora_capture.finish()

    resolved = [
        record for record in records if record.get("event") == "secrets.file.resolved"
    ]
    refused = [
        record for record in records if record.get("event") == "secrets.file.refused"
    ]
    assert len(resolved) == len(refused) == 1
    assert resolved[0].get("level") == "INFO"
    assert refused[0].get("level") == "WARNING"
    assert refused[0]["reason"] == "secret_missing"
    assert set(resolved[0]) == {
        "schema_version",
        "timestamp",
        "level",
        "event",
        "message",
        "component",
        "logger",
        "instance_id",
    }
    assert set(refused[0]) == set(resolved[0]) | {"reason"}
    assert VALUE not in json.dumps(records, ensure_ascii=False)


@pytest.mark.parametrize(
    ("directory", "prefix"),
    [("relative", "CONF_"), ("", "CONF_"), ("/abs\0olute", "CONF_"), ("/absolute", "")],
)
def test_the_configuration_refuses_relative_directory_and_empty_prefix(
    directory: str, prefix: str
) -> None:
    document = {"directory": directory, "namespace_prefixes": {"conformance": [prefix]}}

    with pytest.raises(ValidationError):
        FileSecretsConfiguration.model_validate(document)


def test_list_names_lists_names_only_under_configured_prefixes(
    secret_directory: Path,
) -> None:
    write_secret(secret_directory, "CONF_Z", b"unused")
    write_secret(secret_directory, "CONF_invalid", b"unused")
    write_secret(secret_directory, "OTHER_TOKEN", b"unused")
    subject = provider(secret_directory)

    listed = asyncio.run(subject.list_names("CONF_"))
    narrowed = asyncio.run(subject.list_names("CONF_T"))
    other = asyncio.run(subject.list_names("OTHER_"))
    broad = asyncio.run(subject.list_names("CONF"))
    empty = asyncio.run(subject.list_names(""))

    assert listed == ["CONF_TOKEN", "CONF_Z"]
    assert narrowed == ["CONF_TOKEN"]
    assert other == ["OTHER_TOKEN"]
    assert broad == empty == []


def test_list_names_returns_at_most_a_thousand_names(secret_directory: Path) -> None:
    for index in range(1001):
        write_secret(secret_directory, f"CONF_NAME_{index:04d}", b"unused")
    subject = provider(secret_directory)

    listed = asyncio.run(subject.list_names("CONF_"))

    assert listed == [f"CONF_NAME_{index:04d}" for index in range(1000)]


@pytest.mark.parametrize(
    "case",
    ["directory_missing", "directory_symlink", "directory_mode", "foreign_directory"],
)
def test_list_names_refuses_unreadable_directories(
    secret_directory: Path, case: str
) -> None:
    subject = provider(prepare_refusal(secret_directory, case))

    listed = asyncio.run(subject.list_names("CONF_"))

    assert listed == []


def test_the_installed_entry_point_loads_the_file_provider(
    secret_directory: Path, kora_capture: Capture
) -> None:
    metadata = load_provider(ProtocolName.SECRETS, "file", kora_capture.diagnostics)

    created = metadata.implementation(configuration(secret_directory))

    assert metadata.config_schema == FileSecretsProvider.config_schema
    assert isinstance(created, FileSecretsProvider)


@pytest.mark.parametrize("suffix", ["/", "/.", "/.//./"])
def test_directory_symlinks_with_trailing_separators_are_refused(
    secret_directory: Path, kora_capture: Capture, suffix: str
) -> None:
    link = secret_directory.parent / "link"
    link.symlink_to(secret_directory, target_is_directory=True)
    document = configuration(link)
    document["directory"] = str(link) + suffix
    subject = FileSecretsProvider(FileSecretsConfiguration.model_validate(document))

    with pytest.raises(KoraProtocolError) as raised:
        asyncio.run(
            subject.resolve("secret://file/CONF_TOKEN", context_for(kora_capture))
        )
    listed = asyncio.run(subject.list_names("CONF_"))

    assert raised.value.code == "secret_unreadable"
    assert listed == []


@pytest.mark.parametrize(
    "case",
    [
        "success",
        "missing",
        "oversize",
        "utf8",
        "file_mode",
        "directory_mode",
        "admission_refused",
    ],
)
def test_resolution_closes_directory_and_file_descriptors(
    secret_directory: Path, kora_capture: Capture, case: str
) -> None:
    directory = prepare_refusal(secret_directory, case)
    subject = provider(directory)
    context = context_for(kora_capture)
    paths = (directory, *(path for path in directory.iterdir()))
    before = open_secret_descriptors(paths)

    if case == "success":
        asyncio.run(subject.resolve("secret://file/CONF_TOKEN", context))
    else:
        with pytest.raises(KoraProtocolError):
            asyncio.run(subject.resolve("secret://file/CONF_TOKEN", context))
    after = open_secret_descriptors(paths)

    assert before == after == set()


def test_file_prefixes_have_no_reserved_families(
    secret_directory: Path, kora_capture: Capture
) -> None:
    document = configuration(secret_directory)
    document["namespace_prefixes"] = {"conformance": ["KORA_"]}
    write_secret(secret_directory, "KORA_TOKEN", VALUE.encode())
    subject = FileSecretsProvider(FileSecretsConfiguration.model_validate(document))

    value = asyncio.run(
        subject.resolve("secret://file/KORA_TOKEN", context_for(kora_capture))
    )
    listed = asyncio.run(subject.list_names("KORA_"))

    assert value.reveal() == VALUE
    assert listed == ["KORA_TOKEN"]


def test_a_fifo_in_place_of_the_secret_returns_without_blocking(
    secret_directory: Path,
) -> None:
    directory = prepare_refusal(secret_directory, "fifo")
    outcome: list[str | ReadRefusal] = []
    reader = threading.Thread(
        target=lambda: outcome.append(read_file(str(directory), "CONF_TOKEN")),
        daemon=True,
    )

    reader.start()
    reader.join(timeout=2)

    assert not reader.is_alive()
    assert outcome == [ReadRefusal("secret_unreadable")]
