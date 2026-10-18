import json
import subprocess
from pathlib import Path
from typing import TypeAlias

import pytest

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)

SCHEMA: Path = Path(__file__).resolve().parents[2] / "knowledge/specs/ir.schema.json"


def validate(
    tmp_path: Path, pointer: str, value: JsonValue
) -> subprocess.CompletedProcess[str]:
    schema = tmp_path / "schema.json"
    instance = tmp_path / "instance.json"
    schema.write_text(json.dumps({"$ref": f"{SCHEMA.as_uri()}#/{pointer}"}))
    instance.write_text(json.dumps(value))
    return subprocess.run(
        [
            "uvx",
            "--offline",
            "check-jsonschema@0.38.2",
            "--schemafile",
            str(schema),
            str(instance),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("field", ["retry_on", "non_retryable"])
@pytest.mark.parametrize("error", ["protected_path_modified", "secret_leak"])
def test_protection_errors_are_valid_retry_policy_classes(
    tmp_path: Path, field: str, error: str
) -> None:
    policy: dict[str, JsonValue] = {field: [error]}

    result = validate(tmp_path, "$defs/retry", policy)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("typeBehavior", {"validation": "v", "digest_fields": "d", "summary": "s"}),
        ("typeContract", {"builtin_revision": "1"}),
        ("sourceLocation", {"file": "graph.py", "line": 1}),
        ("retention", {}),
        ("retry", {}),
        ("limits", {}),
        ("inputRef", {"type": "VcsRef"}),
        ("outputDecl", {"type": "VcsRef"}),
        ("sensorPoll", {"sensor": "http", "until": "true"}),
        ("trigger", {"kind": "manual"}),
        ("targetConfig", {"key": {"param": "target"}}),
        ("runtimeSection", {"runtime": "local", "vcs": "git"}),
    ],
)
@pytest.mark.parametrize("key", ["x-tool", "misspelled_field"])
def test_typed_objects_accept_extensions_but_reject_unknown_fields(
    tmp_path: Path, name: str, value: dict[str, JsonValue], key: str
) -> None:
    extended: dict[str, JsonValue] = {**value, key: {"arbitrary": [1, "metadata"]}}

    result = validate(tmp_path, f"$defs/{name}", extended)

    if key == "x-tool":
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Schema validation errors" in result.stdout


@pytest.mark.parametrize(
    ("pointer", "value"),
    [
        ("$defs/trigger/properties/auth", {"kind": "hmac", "secret": "TOKEN"}),
        ("$defs/targetConfig/properties/key", {"param": "target"}),
        (
            "$defs/targetConfig/properties/key",
            {"from": "find_target", "output": "target", "path": "id"},
        ),
        ("$defs/runtimeSection/properties/concurrency", {}),
        ("$defs/humanInputConfig/properties/binds", {}),
        ("$defs/waitEventConfig/properties/event", {"type": "webhook", "name": "done"}),
        ("properties/secrets", {}),
    ],
)
def test_nested_typed_objects_accept_extension_metadata(
    tmp_path: Path, pointer: str, value: dict[str, JsonValue]
) -> None:
    extended: dict[str, JsonValue] = {**value, "x-tool": {"label": "fixture"}}

    result = validate(tmp_path, pointer, extended)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("VcsRef", True),
        ("team/Candidate", True),
        ("List[team/Candidate]", True),
        ("List[List[team/Candidate]]", True),
        ("List[VcsRef", False),
        ("VcsRef]", False),
        ("List[]", False),
        ("List[lowercase]", False),
    ],
)
def test_artifact_type_spelling(tmp_path: Path, value: str, valid: bool) -> None:
    result = validate(tmp_path, "$defs/artifactType", value)

    assert result.returncode == (0 if valid else 1), result.stdout + result.stderr
    if not valid:
        assert "Schema validation errors" in result.stdout


@pytest.mark.parametrize(
    "trigger",
    [
        {"kind": "cron"},
        {"kind": "webhook", "name": "changed"},
        {"kind": "webhook", "auth": {"kind": "hmac", "secret": "TOKEN"}},
        {"kind": "sensor", "interval": "15m"},
        {"kind": "sensor", "poll": {"sensor": "http", "until": "true"}},
        {"kind": "graph", "graph": "upstream"},
        {"kind": "graph", "on": "run_succeeded"},
        {"kind": "manual", "auth": {"kind": "hmac", "secret": "TOKEN"}},
        {"kind": "cron", "expr": "0 6 * * *", "graph": "upstream"},
    ],
)
def test_incomplete_or_incompatible_triggers_are_rejected(
    tmp_path: Path, trigger: dict[str, JsonValue]
) -> None:
    result = validate(tmp_path, "$defs/trigger", trigger)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Schema validation errors" in result.stdout


@pytest.mark.parametrize(
    "trigger",
    [
        {"kind": "manual"},
        {"kind": "cron", "expr": "0 6 * * *", "timezone": "UTC", "params": {}},
        {
            "kind": "webhook",
            "name": "changed",
            "auth": {"kind": "hmac", "secret": "TOKEN"},
        },
        {
            "kind": "sensor",
            "poll": {"sensor": "http", "until": "true"},
            "interval": "15m",
        },
        {"kind": "graph", "graph": "upstream", "on": "run_succeeded"},
    ],
)
def test_complete_trigger_variants_are_accepted(
    tmp_path: Path, trigger: dict[str, JsonValue]
) -> None:
    result = validate(tmp_path, "$defs/trigger", trigger)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "pointer", ["properties/type_contracts", "properties/secrets/properties/aliases"]
)
def test_named_maps_do_not_treat_invalid_entries_as_extension_metadata(
    tmp_path: Path, pointer: str
) -> None:
    value: dict[str, JsonValue] = {"x-tool": {"arbitrary": True}}

    result = validate(tmp_path, pointer, value)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Schema validation errors" in result.stdout
