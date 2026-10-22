import json
import math
from typing import TypeAlias, cast

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]

DEFAULT_MAX_DEPTH: int = 16
MAX_CANONICAL_BYTES: int = 1_048_576
MAX_INTEGER_BITS: int = 14_000
JSON_RULES: frozenset[str] = frozenset(
    {"json_object", "json_depth", "json_key", "json_float", "json_scalar", "json_size"}
)


class JsonShapeError(ValueError):
    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule


class CanonicalBudget:
    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def spend(self, size: int) -> None:
        if size > self.remaining:
            raise JsonShapeError("json_size")
        self.remaining -= size


def parse_json_object(
    value: object,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_bytes: int = MAX_CANONICAL_BYTES,
) -> JsonObject:
    if not isinstance(value, dict):
        raise JsonShapeError("json_object")
    budget = CanonicalBudget(max_bytes)
    return parse_object(cast("dict[object, object]", value), max_depth, 1, budget)


def parse_object(
    value: dict[object, object], max_depth: int, depth: int, budget: CanonicalBudget
) -> JsonObject:
    if depth > max_depth:
        raise JsonShapeError("json_depth")
    budget.spend(2 + max(len(value) - 1, 0))
    parsed: JsonObject = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise JsonShapeError("json_key")
        spend_string(budget, key, "json_key")
        budget.spend(1)
        parsed[key] = parse_value(item, max_depth, depth, budget)
    return parsed


def parse_value(
    value: object, max_depth: int, depth: int, budget: CanonicalBudget
) -> JsonValue:
    if value is None or isinstance(value, bool):
        budget.spend(4 if value is not False else 5)
        return value
    if isinstance(value, int):
        if value.bit_length() > MAX_INTEGER_BITS:
            raise JsonShapeError("json_size")
        budget.spend(len(str(value)))
        return value
    if isinstance(value, str):
        spend_string(budget, value, "json_scalar")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JsonShapeError("json_float")
        budget.spend(len(repr(value)))
        return value
    if isinstance(value, list):
        if depth + 1 > max_depth:
            raise JsonShapeError("json_depth")
        items = cast("list[object]", value)
        budget.spend(2 + max(len(items) - 1, 0))
        return [parse_value(item, max_depth, depth + 1, budget) for item in items]
    if isinstance(value, dict):
        nested = cast("dict[object, object]", value)
        return parse_object(nested, max_depth, depth + 1, budget)
    raise JsonShapeError("json_scalar")


def spend_string(budget: CanonicalBudget, text: str, rule: str) -> None:
    budget.spend(len(text) + 2)
    if text.isascii() and text.isprintable():
        budget.spend(text.count('"') + text.count("\\"))
        return
    encoded = canonical_string(text)
    if encoded is None:
        raise JsonShapeError(rule)
    budget.spend(len(encoded) - len(text) - 2)


def canonical_string(text: str) -> bytes | None:
    try:
        return json.dumps(text, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return None


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
    if parsed is None:
        raise PydanticCustomError("json_object_invalid", "the JSON object is invalid")
    return JsonObjectField(parsed)


def parsed_or_none(value: object) -> JsonObject | None:
    try:
        return parse_json_object(value)
    except JsonShapeError:
        return None
