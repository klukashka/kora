import argparse
import json
import os
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from diag.media_open_controller import (
    POLL_SECONDS,
    REST_SECONDS,
    Arm,
    ExperimentController,
    Trace,
)
from diag.media_open_reader import MediaReader
from diag.media_open_records import (
    duration_bucket,
    exposure_bucket,
    gap_bucket,
    hold_bucket,
    reads_bucket,
    start_bucket,
)
from tests.plugins.storage import pressure_images
from tests.plugins.storage.pressure_controller import create
from tests.plugins.storage.pressure_models import PressureFailure

PAYLOAD_BYTES = 4 * 1024 * 1024
CHILD_SECONDS = 5.0
UNKNOWN_CODES = {"native_release_deadline", "native_completion_unknown"}
HOLDER_SCRIPT = (
    "import os,sys\n"
    "held=os.open(sys.argv[1],os.O_RDONLY)\n"
    "os.write(1,b'1')\n"
    "while sys.stdin.buffer.read(4096):\n"
    "    pass\n"
    "os.close(held)\n"
)


@dataclass
class ControlChild:
    process: subprocess.Popen[bytes] | None = None
    state: str = "na"


def start_child(directory: Path, device: str, child: ControlChild) -> None:
    process = subprocess.Popen(
        (sys.executable, "-I", "-c", HOLDER_SCRIPT, "/dev/r" + device[5:]),
        cwd=directory,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    child.process = process
    if process.stdout is None:
        child.state = "setup_failed"
        raise PressureFailure("control_setup_failed")
    deadline = time.monotonic() + CHILD_SECONDS
    ready = b""
    while not ready and time.monotonic() < deadline:
        readable, _, _ = select.select(
            [process.stdout], [], [], max(0.0, deadline - time.monotonic())
        )
        if readable:
            ready = os.read(process.stdout.fileno(), 1)
            if not ready:
                break
    if ready != b"1":
        child.state = "setup_failed"
        raise PressureFailure("control_setup_failed")


def stop_child(child: ControlChild) -> None:
    process = child.process
    if process is None:
        return
    try:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=CHILD_SECONDS)
            if child.state != "setup_failed":
                child.state = "exited"
        except subprocess.TimeoutExpired:
            child.state = "not_exited"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=CHILD_SECONDS)
        if process.stdout is not None:
            process.stdout.close()


def write_payload(controller: ExperimentController) -> None:
    path = controller.directory / "mounted" / "payload.bin"
    descriptor = os.open(
        path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
    )
    try:
        os.write(descriptor, b"\0" * PAYLOAD_BYTES)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def after_holder(reader: MediaReader, device: str) -> str:
    session, found = reader.session(device)
    try:
        if not found:
            return "unavailable"
        deadline = time.monotonic() + REST_SECONDS
        value = session.read()
        while value == "true" and time.monotonic() < deadline:
            time.sleep(POLL_SECONDS)
            value = session.read()
        return "timeout" if value == "true" else value
    finally:
        session.close()


def a4_cause(directory: Path) -> str:
    path = directory / "native-command-stderr.txt"
    if not path.is_file():
        return "other"
    content = path.read_bytes()[:65536]
    if b"native_admission_deadline" in content:
        return "admission_deadline"
    if any(
        token in content
        for token in (
            b"native_disk_missing",
            b"native_target_missing",
            b"native_session_missing",
        )
    ):
        return "lookup_failed"
    return "other"


def classify(
    trace: Trace,
    controller: ExperimentController,
    failure: str | None,
    acquired: bool,
) -> dict[str, str]:
    request = controller.manifest.native
    started = (
        request is not None and request.phase == "eject" and request.helper is not None
    )
    outcome = request.outcome if started and request is not None else None
    result = None if outcome is None else outcome.result
    if not trace.arm_point:
        row = "P1"
    elif not started:
        if trace.fresh_check in {"differs", "absent", "raised"}:
            row = "A1"
        elif failure in UNKNOWN_CODES:
            row = "A2"
        else:
            row = "A3"
    elif result == "not_requested":
        row = "A4"
    elif result == "refused":
        row = "H2"
    elif outcome is None or failure in UNKNOWN_CODES:
        row = "H3"
    elif failure is None:
        row = "H1"
    else:
        row = "H4"
    code = None if outcome is None else outcome.code
    if row == "H2":
        dissenter = (
            "storagekitd"
            if outcome is not None and outcome.dissenter_comm == "storagekitd"
            else "unknown"
            if outcome is None or outcome.dissenter_pid is None
            else "other"
        )
    else:
        dissenter = "none"
    if failure is None:
        state = "success"
    elif row == "H2":
        state = "refused"
    elif failure == "native_release_deadline":
        state = "release_deadline"
    elif failure == "native_completion_unknown":
        state = "completion_unknown"
    elif not acquired:
        state = "acquire_failure"
    else:
        state = "other"
    return {
        "row": row,
        "helper_started": "yes" if started else "no",
        "outcome": state,
        "failure": "none" if failure is None else failure,
        "code_class": "none"
        if code is None
        else "0"
        if code == 0
        else "49168"
        if code == 49168
        else "other",
        "dissenter_class": dissenter,
        "a4_cause": a4_cause(controller.directory) if row == "A4" else "na",
    }


def run(directory: Path, arm: Arm) -> dict[str, str]:
    reader = MediaReader()
    trace = Trace(arm)
    child = ControlChild()
    failure: str | None = None
    held_by: int | None = None
    acquired = False

    def hook(attachment: pressure_images.Attachment) -> None:
        start_child(directory, attachment.device, child)

    uses_child = arm in {"control", "holder"}
    with create(directory) as base:
        controller = ExperimentController(
            base, arm, reader, trace, hook if uses_child else None
        )
        try:
            try:
                controller.acquire("enospc")
                write_payload(controller)
                acquired = True
                controller.cleanup()
            except PressureFailure as error:
                failure = error.code
        finally:
            if child.process is not None:
                held_by = child.process.pid
            stop_child(child)
        record = classify(trace, controller, failure, acquired)
        request = controller.manifest.native
        outcome = None if request is None else request.outcome
        record["dissenter_is_holder"] = (
            "na"
            if not uses_child
            else "yes"
            if outcome is not None
            and held_by is not None
            and outcome.dissenter_pid == held_by
            else "no"
        )
        record["resources_closed"] = (
            "yes" if controller.manifest.resources_closed else "no"
        )
    after = (
        after_holder(reader, trace.device)
        if arm == "holder" and trace.device and child.state == "exited"
        else "na"
    )
    exposure = (
        None
        if trace.last_read_at is None or trace.helper_spawn_at is None
        else trace.helper_spawn_at - trace.last_read_at
    )
    persisted = trace.clearance == "persisted"
    record.update(
        {
            "arm_point": "yes" if trace.arm_point else "no",
            "first_open": trace.first_open,
            "pre_eject_open": trace.pre_eject_open,
            "pre_helper_open": trace.pre_helper_open,
            "clearance": trace.clearance,
            "hold_bucket": hold_bucket(trace.hold_seconds, persisted),
            "observer": trace.observer,
            "fixture_holder": "yes" if trace.fixture_holder else "no",
            "poll_sleeps": "0" if trace.poll_sleeps == 0 else "1+",
            "max_poll_gap": gap_bucket(trace.loop_gaps),
            "open_reads": reads_bucket(trace.open_reads),
            "observer_duration": duration_bucket(trace.observer_seconds),
            "discover_duration": duration_bucket(trace.discover_seconds),
            "clearance_start_bucket": start_bucket(trace.clearance_start),
            "time_to_clearance_start": start_bucket(trace.clearance_start),
            "last_read_to_helper_start": exposure_bucket(exposure),
            "entry_stable": "yes" if trace.entry_stable else "no",
            "handles_balanced": "yes" if reader.accounting.balanced else "no",
            "fresh_check": trace.fresh_check,
            "control_child": child.state,
            "rest_probe": trace.rest_probe,
            "after_holder_open": after,
        }
    )
    return record


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--arm", choices=("O", "C", "control", "rest", "holder"), required=True
    )
    parser.add_argument("--record", type=Path, required=True)
    arguments = parser.parse_args()
    directory: Path = arguments.directory
    arm: Arm = arguments.arm
    destination: Path = arguments.record
    record = run(directory, arm)
    descriptor = os.open(
        destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        os.write(descriptor, json.dumps(record, sort_keys=True).encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
