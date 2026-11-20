import base64
import csv
import io
import re
from dataclasses import dataclass
from importlib.metadata import Distribution, distributions
from pathlib import Path

from kora.plugins.harness.claude.configuration import SDK_VERSION
from kora.protocols.errors import KoraProtocolError

BINARY_ENTRY = "claude_agent_sdk/_bundled/claude"


@dataclass(frozen=True)
class RecordedFile:
    path: Path
    sha256: str
    size: int


def integrity_error() -> KoraProtocolError:
    return KoraProtocolError(
        "integrity_error",
        "the Claude installation failed integrity verification",
        False,
        {},
    )


def recorded_file(distribution: Distribution, entry: str) -> RecordedFile:
    record = distribution.read_text("RECORD")
    if record is None:
        raise integrity_error()
    try:
        rows = [
            row
            for row in csv.reader(io.StringIO(record), strict=True)
            if row and row[0] == entry
        ]
    except csv.Error:
        raise integrity_error() from None
    if len(rows) != 1 or len(rows[0]) != 3:
        raise integrity_error()
    _, encoded, size_text = rows[0]
    if re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", encoded) is None:
        raise integrity_error()
    digest = base64.urlsafe_b64decode(encoded[7:] + "=")
    if base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii") != encoded[7:]:
        raise integrity_error()
    if re.fullmatch(r"[0-9]{1,19}", size_text) is None:
        raise integrity_error()
    path = Path(str(distribution.locate_file(entry))).absolute()
    return RecordedFile(path, digest.hex(), int(size_text))


def recorded_binary() -> RecordedFile:
    installed = list(distributions(name="claude-agent-sdk"))
    if len(installed) != 1 or installed[0].version != SDK_VERSION:
        raise KoraProtocolError(
            "config_error", "the installed Claude SDK version is unsupported", False, {}
        )
    distribution = installed[0]
    recorded = recorded_file(distribution, BINARY_ENTRY)
    package = Path(str(distribution.locate_file("claude_agent_sdk/__init__.py")))
    binary = package.resolve().parent / "_bundled" / "claude"
    if recorded.path.resolve() != binary.resolve() or not binary.is_file():
        raise KoraProtocolError(
            "config_error", "the installed Claude SDK version is unsupported", False, {}
        )
    return recorded
