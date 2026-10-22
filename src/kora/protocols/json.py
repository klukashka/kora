import json
import math
from dataclasses import dataclass
from typing import TypeAlias, cast

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler, ValidationError
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]

DEFAULT_MAX_DEPTH: int = 16
MAX_FIELD_BYTES: int = 1_048_576
JSON_RULES: frozenset[str] = frozenset(
    {"json_object", "json_depth", "json_key", "json_float", "json_scalar"}
)


class JsonShapeError(ValueError):
    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule


def parse_json_object(value: object, max_depth: int = DEFAULT_MAX_DEPTH) -> JsonObject:
    if not isinstance(value, dict):
        raise JsonShapeError("json_object")
    return parse_object(cast("dict[object, object]", value), max_depth, 1)


def parse_object(value: dict[object, object], max_depth: int, depth: int) -> JsonObject:
    if depth > max_depth:
        raise JsonShapeError("json_depth")
    parsed: JsonObject = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise JsonShapeError("json_key")
        parsed[key] = parse_value(item, max_depth, depth)
    return parsed


def parse_value(value: object, max_depth: int, depth: int) -> JsonValue:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JsonShapeError("json_float")
        return value
    if isinstance(value, list):
        if depth + 1 > max_depth:
            raise JsonShapeError("json_depth")
        return [
            parse_value(item, max_depth, depth + 1)
            for item in cast("list[object]", value)
        ]
    if isinstance(value, dict):
        return parse_object(cast("dict[object, object]", value), max_depth, depth + 1)
    raise JsonShapeError("json_scalar")


class JsonObjectField(dict[str, JsonValue]):
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[object], handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(validate_json_object_field)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> dict[str, str]:
        return {"type": "object"}


def validate_json_object_field(value: object) -> JsonObjectField:
    parsed = parsed_or_none(value)
    size = None if parsed is None else encoded_size_or_none(parsed)
    if parsed is None or size is None or size > MAX_FIELD_BYTES:
        raise PydanticCustomError("json_object_invalid", "the JSON object is invalid")
    return JsonObjectField(parsed)


def encoded_size(value: JsonObject) -> int:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(encoded.encode("utf-8"))


def encoded_size_or_none(value: JsonObject) -> int | None:
    try:
        return encoded_size(value)
    except ValueError:
        return None


def parsed_or_none(value: object) -> JsonObject | None:
    try:
        return parse_json_object(value)
    except JsonShapeError:
        return None


PLACEHOLDER_LOCATION: str = "<field>"


@dataclass(frozen=True)
class SafeFieldError:
    loc: tuple[str | int, ...]
    type: str
    msg: str


def safe_location(
    location: tuple[str | int, ...], known: frozenset[str]
) -> tuple[str | int, ...]:
    return tuple(
        part if isinstance(part, int) or part in known else PLACEHOLDER_LOCATION
        for part in location
    )


def safe_field_errors(
    error: ValidationError, known: frozenset[str], message: str
) -> tuple[SafeFieldError, ...]:
    return tuple(
        SafeFieldError(safe_location(item["loc"], known), item["type"], message)
        for item in error.errors(include_input=False, include_context=False)
    )
