import argparse
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from pydantic import TypeAdapter

from diag import media_open_schedule
from diag.media_open_records import COLUMNS, append, checked
from tests.plugins.storage.pressure_images import DiskImages, image_name
from tests.plugins.storage.pressure_models import PressureManifest

CycleRecord: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, str])
CYCLE_SECONDS = 600
RECOVERY_SECONDS = 120
INVENTORY_SECONDS = 60
LEFTOVER_STOP = 3
LOCAL_FIRST = 10
LOCAL_REST = 50
LOCAL_HOLDER = 50
STOP_ROWS = {"setup_failed", "not_exited"}


def inventory(base: Path) -> set[Path]:
    listed = subprocess.run(
        ("/usr/bin/hdiutil", "info", "-plist"),
        capture_output=True,
        timeout=INVENTORY_SECONDS,
        check=True,
    )
    images = DiskImages.model_validate(plistlib.loads(listed.stdout))
    root = base.resolve()
    attached: set[Path] = set()
    for item in images.images:
        if item.path is None:
            continue
        path = Path(item.path).resolve()
        if path.is_relative_to(root):
            attached.add(path)
    return attached


def leftover_label(count: int) -> str:
    return "3+" if count >= LEFTOVER_STOP else str(count)


def cycle_cleanup(directory: Path, base: Path, errors: Path) -> str:
    if (directory / image_name()).resolve() not in inventory(base):
        return "none_needed"
    manifest = directory / "pressure-manifest.json"
    subprocess.run(
        (
            sys.executable,
            "-m",
            "tests.plugins.storage.pressure_controller",
            "--recover",
            str(manifest),
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=errors.open("wb"),
        timeout=RECOVERY_SECONDS,
        check=False,
    )
    if (directory / image_name()).resolve() not in inventory(base):
        return "released"
    state = PressureManifest.model_validate_json(manifest.read_bytes())
    native = state.native
    if native is not None and native.outcome is not None:
        if native.outcome.result == "refused":
            return "refused"
    return "other"


def run_cycle(
    base: Path, scratch: Path, index: int, arm: str, context: dict[str, str]
) -> dict[str, str]:
    directory = base / f"cycle-{index:03d}"
    record_path = scratch / f"record-{index:03d}.json"
    leftover = len(inventory(base))
    try:
        subprocess.run(
            (
                sys.executable,
                "-m",
                "diag.media_open_cycle",
                "--directory",
                str(directory),
                "--arm",
                arm,
                "--record",
                str(record_path),
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=(scratch / f"cycle-{index:03d}.stderr").open("wb"),
            timeout=CYCLE_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass
    partial = parse_record(record_path) if record_path.is_file() else {}
    cleanup = (
        cycle_cleanup(directory, base, scratch / f"recovery-{index:03d}.stderr")
        if directory.is_dir()
        else "none_needed"
    )
    record = {name: "" for name in COLUMNS}
    record.update(missing_record() if not partial else partial)
    record.update(context)
    record.update(
        {
            "index": str(index),
            "arm": arm,
            "cycle_cleanup": cleanup,
            "leftover_at_start": leftover_label(leftover),
        }
    )
    if cleanup in {"none_needed", "released"} and directory.is_dir():
        shutil.rmtree(directory)
    return checked(record)


def parse_record(path: Path) -> dict[str, str]:
    return CycleRecord.validate_json(path.read_bytes())


def missing_record() -> dict[str, str]:
    return {
        "row": "A3",
        "arm_point": "no",
        "helper_started": "no",
        "outcome": "other",
        "failure": "driver_record_missing",
        "code_class": "none",
        "dissenter_class": "none",
        "dissenter_is_holder": "na",
        "a4_cause": "na",
        "first_open": "none",
        "pre_eject_open": "none",
        "pre_helper_open": "none",
        "clearance": "not_run",
        "hold_bucket": "none",
        "observer": "not_run",
        "fixture_holder": "no",
        "poll_sleeps": "0",
        "max_poll_gap": "none",
        "open_reads": "0",
        "observer_duration": "not_run",
        "discover_duration": "not_run",
        "clearance_start_bucket": "not_run",
        "time_to_clearance_start": "not_run",
        "last_read_to_helper_start": "na",
        "entry_stable": "no",
        "handles_balanced": "no",
        "fresh_check": "not_run",
        "resources_closed": "no",
        "control_child": "na",
        "rest_probe": "na",
        "after_holder_open": "na",
    }


def host_context(mode: str) -> dict[str, str]:
    version = (
        subprocess.run(
            ("/usr/bin/sw_vers", "-productVersion"),
            capture_output=True,
            timeout=30,
            check=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    major = version.split(".", 1)[0]
    image = os.environ.get("ImageVersion", "") if mode == "hosted" else "local"
    if (
        mode == "hosted"
        and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,39}", image) is None
    ):
        image = "unknown"
    return {
        "mode": mode,
        "host": "local" if mode == "local" else major,
        "macos": version,
        "image": image,
    }


def start_load(pids_file: Path) -> list[subprocess.Popen[bytes]]:
    count = os.cpu_count() or 1
    processes = [
        subprocess.Popen(
            ("/usr/bin/yes",),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(count)
    ]
    pids_file.write_text("".join(f"{process.pid}\n" for process in processes))
    return processes


def stop_load(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
    for process in processes:
        process.wait(timeout=30)


def plan(mode: str, schedule: Path | None) -> list[str]:
    if mode == "hosted":
        if schedule is None:
            raise SystemExit("hosted mode needs the schedule file")
        return list(media_open_schedule.read(schedule))
    return ["rest"] * LOCAL_REST + ["holder"] * LOCAL_HOLDER


def local_gate(rows: list[dict[str, str]]) -> bool:
    return all(
        row["rest_probe"] == "pass"
        and row["handles_balanced"] == "yes"
        and row["entry_stable"] == "yes"
        for row in rows
    )


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("hosted", "local"), required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schedule", type=Path)
    parser.add_argument("--load", action="store_true")
    arguments = parser.parse_args()
    mode: str = arguments.mode
    base: Path = arguments.base
    output: Path = arguments.output
    schedule: Path | None = arguments.schedule
    base.mkdir(mode=0o700, parents=True, exist_ok=False)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    scratch = base / "records"
    scratch.mkdir(mode=0o700)
    cycles_path = output / "cycles.csv"
    context = host_context(mode)
    arms = plan(mode, schedule)
    load = start_load(output / "load.pids") if arguments.load else []
    status = "complete"
    recorded: list[dict[str, str]] = []
    try:
        for index, arm in enumerate(arms):
            if len(inventory(base)) >= LEFTOVER_STOP:
                status = "stopped_leftovers"
                break
            record = run_cycle(base, scratch, index, arm, context)
            append(cycles_path, record)
            recorded.append(record)
            if record["control_child"] in STOP_ROWS:
                status = "stopped_control_child"
                break
            if (
                mode == "local"
                and index + 1 == LOCAL_FIRST
                and not local_gate(recorded)
            ):
                status = "stopped_local_first_rest"
                break
    finally:
        stop_load(load)
        summary = {"status": status, "cycles_recorded": len(recorded), **context}
        (output / "run.json").write_text(json.dumps(summary, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
