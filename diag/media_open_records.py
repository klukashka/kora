import csv
import io
import os
import re
from collections.abc import Mapping
from pathlib import Path

OPEN_VALUES = ("true", "false", "unavailable", "none")
YES_NO = ("yes", "no")
DURATION_BUCKETS = ("not_run", "<0.1s", "0.1-0.5s", "0.5-1s", "1-2s", "2-5s", ">=5s")
FIELDS: dict[str, tuple[str, ...] | str] = {
    "index": r"[0-9]{1,4}",
    "mode": ("hosted", "local"),
    "host": ("15", "26", "local"),
    "macos": r"[0-9]{1,3}(\.[0-9]{1,3}){0,2}",
    "image": r"local|[0-9A-Za-z][0-9A-Za-z._-]{0,39}",
    "arm": ("O", "C", "control", "rest", "holder"),
    "row": ("P1", "A1", "A2", "A3", "A4", "H1", "H2", "H3", "H4"),
    "arm_point": YES_NO,
    "helper_started": YES_NO,
    "outcome": (
        "success",
        "refused",
        "release_deadline",
        "completion_unknown",
        "acquire_failure",
        "other",
    ),
    "failure": r"none|[a-z][a-z_]{0,47}",
    "code_class": ("none", "0", "49168", "other"),
    "dissenter_class": ("storagekitd", "other", "none", "unknown"),
    "dissenter_is_holder": ("yes", "no", "na"),
    "a4_cause": ("na", "admission_deadline", "lookup_failed", "other"),
    "first_open": OPEN_VALUES,
    "pre_eject_open": OPEN_VALUES,
    "pre_helper_open": OPEN_VALUES,
    "clearance": (
        "not_run",
        "none",
        "cleared",
        "persisted",
        "unavailable",
        "skipped_fixture_holder",
        "skipped_incomplete",
        "skipped_budget",
    ),
    "hold_bucket": (
        "none",
        "<40ms",
        "40-100ms",
        "0.1-0.5s",
        "0.5-1s",
        "1-2s",
        "2-5s",
        "persisted",
    ),
    "observer": ("not_run", "complete", "incomplete", "unavailable"),
    "fixture_holder": YES_NO,
    "poll_sleeps": ("0", "1+"),
    "max_poll_gap": ("none", "<40ms", "40-100ms", "0.1-0.5s", ">=0.5s"),
    "open_reads": ("0", "1", "2-10", "11-100", "101+"),
    "observer_duration": DURATION_BUCKETS,
    "discover_duration": DURATION_BUCKETS,
    "clearance_start_bucket": (
        "not_run",
        "0-2.5s",
        "2.5-5s",
        "5-7.5s",
        "7.5-10s",
        "10-12.5s",
        "12.5-15s",
        ">=15s",
    ),
    "time_to_clearance_start": (
        "not_run",
        "0-2.5s",
        "2.5-5s",
        "5-7.5s",
        "7.5-10s",
        "10-12.5s",
        "12.5-15s",
        ">=15s",
    ),
    "last_read_to_helper_start": ("na", "<0.5s", "0.5-1s", "1-2s", "2-5s", ">=5s"),
    "entry_stable": YES_NO,
    "handles_balanced": YES_NO,
    "fresh_check": ("not_run", "equal", "differs", "absent", "raised"),
    "resources_closed": YES_NO,
    "cycle_cleanup": ("none_needed", "released", "refused", "other"),
    "leftover_at_start": ("0", "1", "2", "3+"),
    "control_child": ("na", "exited", "setup_failed", "not_exited"),
    "rest_probe": ("na", "pass", "fail"),
    "after_holder_open": ("na", "false", "true", "unavailable", "timeout"),
}
COLUMNS = tuple(FIELDS)


def valid(name: str, value: str) -> bool:
    allowed = FIELDS[name]
    if isinstance(allowed, str):
        return re.fullmatch(allowed, value) is not None
    return value in allowed


def checked(record: Mapping[str, str]) -> dict[str, str]:
    if tuple(record) != COLUMNS:
        raise ValueError("record columns differ from the fixed schema")
    for name, value in record.items():
        if not valid(name, value):
            raise ValueError("record value outside the fixed schema: " + name)
    return dict(record)


def duration_bucket(seconds: float | None) -> str:
    if seconds is None:
        return "not_run"
    for limit, label in (
        (0.1, "<0.1s"),
        (0.5, "0.1-0.5s"),
        (1, "0.5-1s"),
        (2, "1-2s"),
        (5, "2-5s"),
    ):
        if seconds < limit:
            return label
    return ">=5s"


def hold_bucket(seconds: float | None, persisted: bool) -> str:
    if persisted:
        return "persisted"
    if seconds is None:
        return "none"
    for limit, label in (
        (0.04, "<40ms"),
        (0.1, "40-100ms"),
        (0.5, "0.1-0.5s"),
        (1, "0.5-1s"),
        (2, "1-2s"),
    ):
        if seconds < limit:
            return label
    return "2-5s"


def gap_bucket(gaps: list[float]) -> str:
    if not gaps:
        return "none"
    largest = max(gaps)
    for limit, label in ((0.04, "<40ms"), (0.1, "40-100ms"), (0.5, "0.1-0.5s")):
        if largest < limit:
            return label
    return ">=0.5s"


def reads_bucket(count: int) -> str:
    if count <= 1:
        return str(count)
    if count <= 10:
        return "2-10"
    if count <= 100:
        return "11-100"
    return "101+"


def start_bucket(seconds: float | None) -> str:
    if seconds is None:
        return "not_run"
    steps = ("0-2.5s", "2.5-5s", "5-7.5s", "7.5-10s", "10-12.5s", "12.5-15s")
    index = int(max(0.0, seconds) // 2.5)
    return steps[index] if index < len(steps) else ">=15s"


def exposure_bucket(seconds: float | None) -> str:
    if seconds is None:
        return "na"
    for limit, label in ((0.5, "<0.5s"), (1, "0.5-1s"), (2, "1-2s"), (5, "2-5s")):
        if seconds < limit:
            return label
    return ">=5s"


def header() -> bytes:
    stream = io.StringIO()
    csv.writer(stream, lineterminator="\n").writerow(COLUMNS)
    return stream.getvalue().encode()


def append(path: Path, record: Mapping[str, str]) -> None:
    row = checked(record)
    stream = io.StringIO()
    csv.writer(stream, lineterminator="\n").writerow([row[name] for name in COLUMNS])
    descriptor = os.open(
        path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, header())
        os.write(descriptor, stream.getvalue().encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="ascii") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError("cycle file columns differ from the fixed schema")
        return [checked(row) for row in reader]
