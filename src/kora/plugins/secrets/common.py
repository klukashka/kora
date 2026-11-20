import re
from collections.abc import Mapping
from typing import Annotated, Final, NoReturn

from pydantic import StringConstraints

from kora.observability import EventSpec, SecretAdmissionError
from kora.protocols.common import ProtocolContext
from kora.protocols.errors import KoraProtocolError
from kora.protocols.secret_value import SecretAdmission, SecretValue

NAME_BODY: Final[str] = r"[A-Z_][A-Z0-9_]{0,127}"
NAME_PATTERN: Final[str] = f"^{NAME_BODY}$"
NAME: Final[re.Pattern[str]] = re.compile(NAME_BODY)
Name = Annotated[str, StringConstraints(strict=True, pattern=NAME_PATTERN)]


def readable(
    prefixes: Mapping[str, tuple[str, ...]], namespace: str, name: str
) -> bool:
    return any(name.startswith(prefix) for prefix in prefixes.get(namespace, ()))


def refused(
    ctx: ProtocolContext, code: str, event: EventSpec, refusals: Mapping[str, str]
) -> NoReturn:
    ctx.logger.emit(event, reason=code)
    raise KoraProtocolError(code, refusals[code], False, {}) from None


def admitted(admission: SecretAdmission, value: str) -> SecretValue | None:
    try:
        return admission.admit(value)
    except SecretAdmissionError:
        return None
