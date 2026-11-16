import asyncio
import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from kora.plugins.harness.claude.session import (
    close_recorded_processes,
    interpreter,
    launcher_text,
)
from kora.protocols.deadlines import NANOSECONDS_PER_SECOND
from kora.protocols.time import Clock, SystemClock
from tests.plugins.harness.claude.contained import assert_no_outbound
from tests.plugins.harness.claude.scripted_model import KEY, mapping, objects
from tests.plugins.harness.claude.test_session_isolation import run_scenario

PROMPT: Final[str] = "conformance"
SCRIPTED: Final[str] = "scripted-bytes\n"
SEEDED: Final[str] = "seeded-bytes\n"


def prepare_scenario(root: Path) -> tuple[Path, Path, Path]:
    workspace, account, markers = (
        root / name for name in ("workspace", "account", "markers")
    )
    for path in (workspace, account, markers):
        path.mkdir()
    (workspace / "seeded.txt").write_text(SEEDED)
    return workspace, account, markers


def event_data(report: dict[str, object], kind: str) -> list[dict[str, object]]:
    events = objects(report["events"])
    assert events is not None
    found: list[dict[str, object]] = []
    for value in events:
        event = mapping(value)
        assert event is not None
        if event["kind"] == kind:
            data = mapping(event["data"])
            assert data is not None
            found.append(data)
    return found


def assert_diagnostics(report: dict[str, object], outcome: str, target: Path) -> None:
    records = objects(report["diagnostics"])
    assert records is not None
    finished = [
        record
        for value in records
        if (record := mapping(value)) is not None
        and record.get("event") == "harness.claude.session_finished"
    ]
    assert len(finished) == 1
    assert finished[0]["outcome"] == outcome
    rendered = json.dumps(records)
    for secret in (KEY, PROMPT, SCRIPTED, str(target)):
        assert json.dumps(secret)[1:-1] not in rendered


def test_scripted_claude_write_changes_the_file_when_allowed(tmp_path: Path) -> None:
    workspace, account, markers = prepare_scenario(tmp_path)
    target = workspace / "target.txt"

    report = run_scenario(
        tmp_path,
        workspace,
        account,
        markers,
        [("Write", {"file_path": str(target), "content": SCRIPTED})],
    )

    assert_no_outbound(report)
    assert report["cause"] == "completed"
    assert report["error_class"] is None
    assert target.read_text() == SCRIPTED
    assert (workspace / "seeded.txt").read_text() == SEEDED
    calls = event_data(report, "tool_call")
    results = event_data(report, "tool_result")
    assert len(calls) == len(results) == 1
    assert calls[0]["tool"] == "Write"
    assert results[0]["tool_use_id"] == calls[0]["tool_use_id"]
    assert results[0]["is_error"] is False
    assert event_data(report, "permission_denied") == []
    assert_diagnostics(report, "completed", target)
    assert set(objects(report["clients"]) or []) == {"127.0.0.1"}


def test_scripted_claude_write_leaves_the_file_untouched_when_denied(
    tmp_path: Path,
) -> None:
    workspace, account, markers = prepare_scenario(tmp_path)
    target = workspace / "target.txt"

    report = run_scenario(
        tmp_path,
        workspace,
        account,
        markers,
        [("Write", {"file_path": str(target), "content": SCRIPTED})],
        denied_tool="Write",
    )

    assert_no_outbound(report)
    assert report["cause"] == "completed"
    assert report["error_class"] is None
    assert not target.exists()
    denials = event_data(report, "permission_denied")
    assert len(denials) == 1
    assert denials[0]["source"] == "policy"
    assert denials[0]["tool"] == "Write"
    assert (workspace / "seeded.txt").read_text() == SEEDED
    assert_diagnostics(report, "completed", target)


def test_scripted_claude_decision_failure_leaves_the_file_untouched(
    tmp_path: Path,
) -> None:
    workspace, account, markers = prepare_scenario(tmp_path)
    target = workspace / "target.txt"

    report = run_scenario(
        tmp_path,
        workspace,
        account,
        markers,
        [("Write", {"file_path": str(target), "content": SCRIPTED})],
        policy="raising",
    )

    assert_no_outbound(report)
    assert not target.exists()
    assert report["cause"] == "policy_error"
    assert report["error_class"] == "config_error"
    assert "kora-decision-failure-1646" not in json.dumps(report)
    assert (workspace / "seeded.txt").read_text() == SEEDED
    assert_diagnostics(report, "policy_error", target)


def test_scripted_claude_cancellation_closes_the_tool_child(tmp_path: Path) -> None:
    workspace, account, markers = prepare_scenario(tmp_path)
    started = markers / "started"
    target = workspace / "target.txt"
    command = (
        f"touch {shlex.quote(str(started))}; sleep 20; "
        f"printf ran > {shlex.quote(str(target))}"
    )

    report = run_scenario(
        tmp_path,
        workspace,
        account,
        markers,
        [("Bash", {"command": command})],
        cancel_marker=str(started),
    )

    assert_no_outbound(report)
    assert report["cause"] == "cancel"
    assert report["error_class"] == "cancelled"
    assert started.is_file()
    assert not target.exists()
    assert (workspace / "seeded.txt").read_text() == SEEDED
    assert workspace_quiet(workspace, SystemClock())
    assert_diagnostics(report, "cancel", target)


def workspace_quiet(workspace: Path, clock: Clock) -> bool:
    deadline = clock.monotonic_ns() + 5 * NANOSECONDS_PER_SECOND
    while True:
        listed = subprocess.run(
            ["/bin/ps", "-axww", "-o", "command="],
            capture_output=True,
            text=True,
            check=True,
        )
        if str(workspace) not in listed.stdout:
            return True
        if clock.monotonic_ns() >= deadline:
            return False
        asyncio.run(clock.sleep_until(min(deadline, clock.monotonic_ns() + 50_000_000)))


@pytest.mark.asyncio
async def test_scripted_claude_session_kills_a_child_that_ignores_termination(
    tmp_path: Path,
) -> None:
    clock = SystemClock()
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    hold = launch_dir / "hold.py"
    ready = launch_dir / "ready"
    hold.write_text(
        "import pathlib\nimport signal\nimport time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(" + repr(str(ready)) + ").write_text('ready', encoding='ascii')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, str(hold)],
        cwd=launch_dir,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (launch_dir / "pids").write_text(f"{process.pid}\n", encoding="ascii")

    try:
        deadline = clock.monotonic_ns() + 2 * NANOSECONDS_PER_SECOND
        while not ready.is_file() and clock.monotonic_ns() < deadline:
            await clock.sleep_until(min(deadline, clock.monotonic_ns() + 50_000_000))
        if not ready.is_file():
            raise AssertionError("the child did not ignore termination")
        closed = await close_recorded_processes(launch_dir, sys.executable, clock)
        finished = process.wait(timeout=2)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)

    assert closed is True
    assert finished == -signal.SIGKILL
    assert process.returncode == -signal.SIGKILL


def test_scripted_launcher_starts_isolated_and_skips_the_version_probe(
    tmp_path: Path,
) -> None:
    executable = interpreter()
    poison = tmp_path / "poison"
    poison.mkdir()
    marker = tmp_path / "poisoned"
    (poison / "json.py").write_text(
        "import pathlib\npathlib.Path("
        + repr(str(marker))
        + ").write_text('x', encoding='utf-8')\n",
        encoding="utf-8",
    )
    launch = tmp_path / "launch"
    launch.mkdir()
    ran = tmp_path / "ran"
    child = tmp_path / "child.py"
    child.write_text(
        f"#!{executable}\nimport pathlib\nimport sys\n"
        "pathlib.Path(sys.argv[1]).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    child.chmod(0o700)
    (launch / "binary").write_text(f"{child}\n", encoding="utf-8")
    (launch / "environment.json").write_text(
        '{"PATH": "/usr/bin:/bin"}\n', encoding="utf-8"
    )
    launcher = tmp_path / "launcher"
    launcher.write_text(launcher_text(executable), encoding="utf-8")
    launcher.chmod(0o700)
    inherited = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(poison),
        "KORA_CLAUDE_LAUNCH_DIR": str(launch),
    }

    version = subprocess.run(
        [str(launcher), "-v"],
        check=False,
        capture_output=True,
        env=inherited,
        shell=False,
    )
    assert version.returncode == 0
    assert not (launch / "pids").exists()
    assert not marker.exists()

    completed = subprocess.run(
        [str(launcher), str(ran)],
        check=False,
        capture_output=True,
        env=inherited,
        shell=False,
    )
    assert completed.returncode == 0
    assert ran.read_text(encoding="utf-8") == "ran"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_scripted_cleanup_leaves_a_process_that_only_names_the_workspace(
    tmp_path: Path,
) -> None:
    clock = SystemClock()
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    hold = work / "hold.py"
    hold.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(hold)],
        cwd=work,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        closed = await close_recorded_processes(launch_dir, "/bundled/claude", clock)
        assert closed is True
        assert process.poll() is None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)


async def process_gone(pid: int, clock: Clock) -> bool:
    deadline = clock.monotonic_ns() + NANOSECONDS_PER_SECOND
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        if clock.monotonic_ns() >= deadline:
            return False
        await clock.sleep_until(min(deadline, clock.monotonic_ns() + 50_000_000))


@pytest.mark.asyncio
async def test_scripted_cleanup_kills_an_unmarked_member_that_ignores_termination(
    tmp_path: Path,
) -> None:
    clock = SystemClock()
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    ready = outside / "ready"
    child_script = outside / "child.py"
    child_script.write_text(
        "import os\nimport pathlib\nimport signal\nimport sys\nimport time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "ready = pathlib.Path(sys.argv[1])\n"
        "staged = ready.with_suffix('.pending')\n"
        "staged.write_text(str(os.getpid()), encoding='ascii')\n"
        "staged.replace(ready)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    leader_script = outside / "leader.py"
    leader_script.write_text(
        "import subprocess\nimport sys\nimport time\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    leader = subprocess.Popen(
        [sys.executable, str(leader_script), str(child_script), str(ready)],
        cwd=outside,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (launch_dir / "pids").write_text(f"{leader.pid}\n", encoding="ascii")
    child_pid = 0

    try:
        deadline = clock.monotonic_ns() + 2 * NANOSECONDS_PER_SECOND
        while not ready.is_file() and clock.monotonic_ns() < deadline:
            await clock.sleep_until(min(deadline, clock.monotonic_ns() + 50_000_000))
        if not ready.is_file():
            raise AssertionError("the unmarked child did not start")
        child_pid = int(ready.read_text(encoding="ascii"))

        closed = await close_recorded_processes(launch_dir, sys.executable, clock)
        assert closed is True
        assert await process_gone(child_pid, clock)
    finally:
        if leader.poll() is None:
            os.killpg(leader.pid, signal.SIGKILL)
            leader.wait(timeout=2)
        if child_pid > 0:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
