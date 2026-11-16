from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, cast

from pydantic import TypeAdapter, ValidationError

from kora.ir.cost import Identifier as CostIdentifier
from kora.plugins.harness.claude.stop import refused
from kora.protocols.harness import ProviderLimitedData, ProviderRetryingData

MAX_LIMIT_EVENTS: Final[int] = 16
MODEL: Final[TypeAdapter[str]] = TypeAdapter(CostIdentifier)


class ProviderSignals:
    def __init__(
        self,
        binding: str,
        max_retries: int,
        observed_at: Callable[[], datetime],
        drop: Callable[[str], None],
    ) -> None:
        self._binding = binding
        self._max_retries = max_retries
        self._observed_at = observed_at
        self._drop = drop
        self._model: str | None = None
        self._message_id: str | None = None
        self._retries = 0
        self._limit_key: tuple[str, str | None, datetime | None] | None = None
        self._limits = 0

    def model(self, value: object) -> None:
        if value is None:
            return
        try:
            model = MODEL.validate_python(value)
        except ValidationError:
            model = None
        if model is None:
            refused()
        self._model = model

    def assistant(self, message_id: str | None, model: object) -> None:
        self.model(model)
        if message_id is not None and message_id != self._message_id:
            self._message_id = message_id
            self._retries = 0

    def current_model(self) -> str:
        if self._model is None:
            refused()
        return self._model

    def rejected(
        self, resets_at: object, rate_limit_type: object
    ) -> ProviderLimitedData | None:
        if resets_at is not None and type(resets_at) is not int:
            refused()
        try:
            retry_after = (
                None
                if resets_at is None
                else datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=resets_at)
            )
        except OverflowError:
            refused()
        return self.limit("quota", rate_limit_type, retry_after)

    def rate(self) -> ProviderLimitedData | None:
        if self._limits:
            return None
        return self.limit("rate", None, None)

    def limit(
        self,
        scope: Literal["rate", "quota"],
        vendor_group: object,
        retry_after: datetime | None,
    ) -> ProviderLimitedData | None:
        model = self.current_model()
        try:
            data = ProviderLimitedData.model_validate(
                {
                    "scope": scope,
                    "vendor_group": vendor_group,
                    "retry_after": retry_after,
                    "model": model,
                    "binding": self._binding,
                }
            )
        except ValidationError:
            data = None
        if data is None:
            refused()
        if self._limits >= MAX_LIMIT_EVENTS:
            self._drop("message")
            return None
        key = (data.scope, data.vendor_group, data.retry_after)
        if key == self._limit_key:
            self._drop("message")
            return None
        self._limit_key = key
        self._limits += 1
        return data

    def retry(self, value: object) -> ProviderRetryingData | None:
        if not isinstance(value, dict):
            refused()
        fields = cast("dict[object, object]", value)
        attempt = fields.get("attempt")
        if type(attempt) is not int or attempt < 0:
            refused()
        model = self.current_model()
        delay = fields.get("retry_delay_ms")
        try:
            retry_after = (
                self._observed_at() + timedelta(milliseconds=delay)
                if type(delay) is int and delay >= 0
                else None
            )
            data = ProviderRetryingData(
                model=model, retry_number=attempt, retry_after=retry_after
            )
        except (OverflowError, ValidationError):
            data = None
        if data is None:
            refused()
        if self._retries >= self._max_retries:
            self._drop("system")
            return None
        self._retries += 1
        return data
