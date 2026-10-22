from enum import StrEnum
from typing import Final, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    SerializerFunctionWrapHandler,
    StrictStr,
    model_serializer,
    model_validator,
)
from pydantic.config import JsonDict
from pydantic_core import PydanticCustomError

from kora.ir.json import JsonObjectField

EXTENSION_PREFIX: Final[str] = "x-"
EXTENSIONS_FIELD: Final[str] = "extensions"
POLICY_FIELDS: Final[frozenset[str]] = frozenset({"allow", "deny"})


class ErrorClass(StrEnum):
    HARNESS_ERROR = "harness_error"
    COMMAND_FAILED = "command_failed"
    TIMEOUT = "timeout"
    EVENTS_EXHAUSTED = "events_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ARTIFACT_INVALID = "artifact_invalid"
    ATTEMPT_FAILED = "attempt_failed"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
    CANDIDATE_MOVED = "candidate_moved"
    WAIT_EXPIRED = "wait_expired"
    STALLED = "stalled"
    PROVIDER_LIMITED = "provider_limited"
    PROTECTED_PATH_MODIFIED = "protected_path_modified"
    CREDENTIAL_LEAK = "secret_leak"
    INTEGRITY_ERROR = "integrity_error"
    INFRASTRUCTURE = "infrastructure"
    CANCELLED = "cancelled"
    CONFIG_ERROR = "config_error"


def extension_schema(schema: JsonDict) -> None:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        properties.pop(EXTENSIONS_FIELD, None)
    schema["patternProperties"] = {f"^{EXTENSION_PREFIX}": {}}
    schema["additionalProperties"] = False


def split_extensions(value: dict[object, object]) -> dict[str, object]:
    fields: dict[str, object] = {}
    extensions: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str) and key.startswith(EXTENSION_PREFIX):
            extensions[key] = item
        elif isinstance(key, str) and key in POLICY_FIELDS:
            fields[key] = item
        else:
            raise PydanticCustomError(
                "tool_policy_field_unknown", "the tool policy has an unknown field"
            )
    return {**fields, EXTENSIONS_FIELD: extensions}


class ToolPolicy(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        json_schema_extra=extension_schema,
    )

    allow: tuple[StrictStr, ...] = ()
    deny: tuple[StrictStr, ...] = ()
    extensions: JsonObjectField = Field(default_factory=JsonObjectField)

    @model_validator(mode="wrap")
    @classmethod
    def read_extensions(
        cls, value: object, handler: ModelWrapValidatorHandler["ToolPolicy"]
    ) -> "ToolPolicy":
        if not isinstance(value, dict):
            return handler(value)
        return handler(split_extensions(cast("dict[object, object]", value)))

    @model_serializer(mode="wrap")
    def write_extensions(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, object]:
        serialised: object = handler(self)
        data = dict(cast("dict[str, object]", serialised))
        extensions = data.pop(EXTENSIONS_FIELD, {})
        return {**data, **cast("dict[str, object]", extensions)}
