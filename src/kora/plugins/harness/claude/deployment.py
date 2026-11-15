import re
from typing import Annotated, Final, Self

from pydantic import (
    MISSING,
    BeforeValidator,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)
from pydantic_core import PydanticCustomError

from kora.ir.config import environment_schema, require_integer
from kora.ir.json import JsonObject
from kora.ir.scalars import SecretRef
from kora.protocols.boundary import BoundaryModel
from kora.protocols.metadata import parse_config_schema

MAX_RELEASE_BATCH: Final[int] = 1000
MAX_RELEASE_INTERVAL: Final[int] = 86400
MAX_RETRIES: Final[int] = 10
QuotaIdentifier = Annotated[
    StrictStr, Field(pattern=re.compile(r"^[\x21-\x7e]{1,256}(?![\s\S])"))
]
ModelPatterns = Annotated[tuple[QuotaIdentifier, ...], Field(min_length=1)]


def default_groups() -> dict[str, tuple[str, ...]]:
    return {
        "account": ("*",),
        "opus": ("claude-opus-*",),
        "sonnet": ("claude-sonnet-*",),
    }


def default_vendor_groups() -> dict[str, str]:
    return {
        "five_hour": "account",
        "seven_day": "account",
        "seven_day_opus": "opus",
        "seven_day_sonnet": "sonnet",
    }


class ClaudeCredentials(BoundaryModel):
    ANTHROPIC_API_KEY: SecretRef


class ClaudeQuota(BoundaryModel):
    resource: QuotaIdentifier | MISSING = MISSING
    groups: Annotated[
        dict[QuotaIdentifier, ModelPatterns],
        Field(json_schema_extra=environment_schema),
    ] = Field(default_factory=default_groups)
    vendor_groups: Annotated[
        dict[QuotaIdentifier, QuotaIdentifier],
        Field(json_schema_extra=environment_schema),
    ] = Field(default_factory=default_vendor_groups)
    release_batch: Annotated[
        StrictInt, Field(ge=1, le=MAX_RELEASE_BATCH), BeforeValidator(require_integer)
    ] = 1
    release_interval: Annotated[
        StrictFloat, Field(gt=0, le=MAX_RELEASE_INTERVAL, allow_inf_nan=False)
    ] = 1.0

    @model_validator(mode="after")
    def declared_vendor_targets(self) -> Self:
        if any(group not in self.groups for group in self.vendor_groups.values()):
            raise PydanticCustomError(
                "quota_group_unknown",
                "a vendor limit group targets an undeclared group",
            )
        return self


class ClaudeDeployment(BoundaryModel):
    credentials: ClaudeCredentials
    quota: ClaudeQuota = Field(default_factory=ClaudeQuota)
    cli_telemetry: StrictBool = False
    max_retries: Annotated[
        StrictInt, Field(ge=0, le=MAX_RETRIES), BeforeValidator(require_integer)
    ] = 2


CONFIG_SCHEMA: Final[JsonObject] = parse_config_schema(
    ClaudeDeployment.model_json_schema()
)
