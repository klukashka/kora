import asyncio
import os
import re
import signal
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final, NoReturn

from kora.executor.launcher import ExitWatch
from kora.observability import CATALOG, EventSpec, FieldKind, Level
from kora.plugins.harness.claude.sdk import LAUNCH_DIRECTORY
from kora.protocols.deadlines import NANOSECONDS_PER_SECOND, duration_ns
from kora.protocols.errors import KoraProtocolError
from kora.protocols.time import Clock

COMPONENT: Final[str] = CATALOG.register_component("harness.claude")
SESSION_FINISHED: Final[EventSpec] = CATALOG.register(
    EventSpec(
        "harness.claude.session_finished",
        COMPONENT,
        Level.INFO,
        "a scripted Claude session finished",
        {"outcome": FieldKind.STRING},
    )
)
ENV_NAME: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
TOOL_NAME: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
OWNED_ENVIRONMENT: Final[frozenset[str]] = frozenset(
    {"ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", LAUNCH_DIRECTORY}
)
POLL_NS: Final[int] = 50_000_000
SIGNAL_WAIT_NS: Final[int] = NANOSECONDS_PER_SECOND
MAX_ENVIRONMENT: Final[int] = 64
MAX_ENVIRONMENT_VALUE: Final[int] = 4096
MAX_PROMPT: Final[int] = 2000
MAX_TOOLS: Final[int] = 8
MAX_INTERPRETER: Final[int] = 120
MIN_BOUND_NS: Final[int] = NANOSECONDS_PER_SECOND
MAX_BOUND_NS: Final[int] = 60 * NANOSECONDS_PER_SECOND
FILE_FLAGS: Final[int] = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
)


class SessionFailure(StrEnum):
    LAUNCH_REJECTED = "launch_rejected"
    SESSION_FAILED = "session_failed"
    CHILDREN_REMAINING = "children_remaining"


@dataclass(frozen=True)
class ProcessRow:
    pid: int
    ppid: int
    pgid: int
    stat: str
    command: str


def launch_rejected() -> NoReturn:
    raise KoraProtocolError(
        "launch_rejected", "the Claude launch was rejected", False, {}
    )


def session_failed() -> NoReturn:
    raise KoraProtocolError("harness_error", "the Claude session failed", False, {})


def require_prompt(prompt: str) -> None:
    if not 1 <= len(prompt) <= MAX_PROMPT or "\x00" in prompt:
        launch_rejected()


def require_tools(tools: tuple[str, ...]) -> None:
    if not 1 <= len(tools) <= MAX_TOOLS or len(set(tools)) != len(tools):
        launch_rejected()
    if any(TOOL_NAME.fullmatch(name) is None for name in tools):
        launch_rejected()


def require_bound(bound: timedelta) -> int:
    span = duration_ns(bound)
    if span < MIN_BOUND_NS or span > MAX_BOUND_NS:
        launch_rejected()
    return span


def require_environment_value(value: str) -> None:
    if (
        value == ""
        or len(value) > MAX_ENVIRONMENT_VALUE
        or any(ord(character) < 32 for character in value)
    ):
        launch_rejected()


def require_environment(environment: Mapping[str, str]) -> dict[str, str]:
    if len(environment) > MAX_ENVIRONMENT:
        launch_rejected()
    prepared: dict[str, str] = {}
    for name, value in environment.items():
        if ENV_NAME.fullmatch(name) is None or name in OWNED_ENVIRONMENT:
            launch_rejected()
        require_environment_value(value)
        prepared[name] = value
    return prepared


def require_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError:
        launch_rejected()
    if not stat.S_ISDIR(info.st_mode):
        launch_rejected()


def require_launch_directory(path: Path) -> None:
    if not path.is_absolute() or path.name == "":
        launch_rejected()
    require_directory(path.parent)
    try:
        path.lstat()
    except FileNotFoundError:
        return
    launch_rejected()


def interpreter() -> str:
    executable = sys.executable
    if (
        len(executable) > MAX_INTERPRETER
        or not executable.startswith("/")
        or any(character in executable for character in ("\n", "\r", " "))
    ):
        launch_rejected()
    return executable


def launcher_text(executable: str) -> str:
    lines = (
        "import sys",
        "if sys.argv[1:] == ['-v']:",
        "    raise SystemExit(0)",
        "import json",
        "import os",
        "from pathlib import Path",
        "os.setsid()",
        "root = Path(os.environ['KORA_CLAUDE_LAUNCH_DIR'])",
        "binary = (root / 'binary').read_text(encoding='utf-8').strip()",
        "loaded = json.loads((root / 'environment.json').read_text(encoding='utf-8'))",
        "environment = {str(key): str(value) for key, value in loaded.items()}",
        "with (root / 'pids').open('a', encoding='ascii') as handle:",
        '    handle.write(f"{os.getpid()}\\n")',
        "limit = min(int(os.sysconf('SC_OPEN_MAX')), 65536)",
        "os.closerange(3, limit)",
        "os.execve(binary, [binary, *sys.argv[1:]], environment)",
    )
    return f"#!{executable} -I\n" + "\n".join(lines) + "\n"


def write_exclusive(path: Path, data: bytes, mode: int) -> None:
    try:
        descriptor = os.open(path, FILE_FLAGS, mode)
    except OSError:
        launch_rejected()
    failed = False
    try:
        view = data
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                failed = True
                break
            view = view[written:]
        os.fchmod(descriptor, mode)
    except OSError:
        failed = True
    finally:
        os.close(descriptor)
    if failed:
        launch_rejected()


def remove_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        session_failed()
    path.unlink()


def process_rows() -> tuple[ProcessRow, ...] | None:
    completed = subprocess.run(
        ["/bin/ps", "-axww", "-o", "pid=,ppid=,pgid=,stat=,command="],
        check=False,
        capture_output=True,
        shell=False,
    )
    if completed.returncode != 0:
        return None
    rows: list[ProcessRow] = []
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split(None, 4)
        if len(parts) != 5:
            continue
        pid_text, ppid_text, pgid_text, stat_text, command = parts
        if pid_text.isdecimal() and ppid_text.isdecimal() and pgid_text.isdecimal():
            rows.append(
                ProcessRow(
                    int(pid_text),
                    int(ppid_text),
                    int(pgid_text),
                    stat_text,
                    command,
                )
            )
    return tuple(rows)


def recorded_pids(launch_dir: Path) -> set[int]:
    path = launch_dir / "pids"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return set()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return set()
    found: set[int] = set()
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if line.isdecimal():
            found.add(int(line))
    return found


def eligible(
    row: ProcessRow, recorded: set[int], markers: tuple[str, ...], binary: str
) -> bool:
    if row.stat.startswith("Z") or row.pgid <= 1:
        return False
    if any(marker != "" and marker in row.command for marker in markers):
        return True
    return row.pid in recorded and binary != "" and binary in row.command


def eligible_rows(
    launch_dir: Path, markers: tuple[str, ...], binary: str
) -> tuple[ProcessRow, ...] | None:
    rows = process_rows()
    if rows is None:
        return None
    recorded = recorded_pids(launch_dir)
    return tuple(row for row in rows if eligible(row, recorded, markers, binary))


def living_groups(groups: set[int], own: int) -> set[int] | None:
    rows = process_rows()
    if rows is None:
        return None
    living: set[int] = set()
    for row in rows:
        if row.pgid == own or row.pgid <= 1 or row.stat.startswith("Z"):
            continue
        if row.pgid in groups:
            living.add(row.pgid)
    return living


def signal_groups(groups: set[int], number: int) -> None:
    for pgid in groups:
        try:
            os.killpg(pgid, number)
        except (ProcessLookupError, PermissionError):
            continue


async def settle(task: asyncio.Task[object]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return


async def pause_until(
    pids: set[int], clock: Clock, deadline_ns: int, gone: Callable[[], bool]
) -> None:
    loop = asyncio.get_running_loop()
    watches: list[ExitWatch] = []
    try:
        for pid in pids:
            if pid <= 0:
                continue
            try:
                watches.append(ExitWatch(loop, pid))
            except ProcessLookupError:
                continue
        while not gone() and clock.monotonic_ns() < deadline_ns:
            pending = [
                asyncio.create_task(watch.exited.wait())
                for watch in watches
                if not watch.exited.is_set()
            ]
            step = min(deadline_ns, clock.monotonic_ns() + POLL_NS)
            sleep = asyncio.create_task(clock.sleep_until(step))
            waiting = [sleep, *pending]
            try:
                await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in waiting:
                    if not task.done():
                        task.cancel()
                    await settle(task)
    finally:
        for watch in watches:
            watch.close()


async def close_recorded_processes(launch_dir: Path, binary: str, clock: Clock) -> bool:
    markers = (str(launch_dir),)
    own = os.getpgrp()
    signaled: set[int] = set()
    for number in (signal.SIGTERM, signal.SIGKILL):
        rows = eligible_rows(launch_dir, markers, binary)
        if rows is None:
            return False
        groups = {row.pgid for row in rows if row.pgid != own and row.pgid > 1}
        groups.update(pgid for pgid in signaled if pgid != own and pgid > 1)
        if not groups:
            if rows:
                return False
            return living_groups(signaled, own) == set()
        signaled.update(groups)
        signal_groups(groups, number)
        await pause_until(
            recorded_pids(launch_dir),
            clock,
            clock.monotonic_ns() + SIGNAL_WAIT_NS,
            lambda: living_groups(signaled, own) == set(),
        )
    return living_groups(signaled, own) == set()


def failure_for(error: KoraProtocolError) -> SessionFailure:
    if error.code == "launch_rejected":
        return SessionFailure.LAUNCH_REJECTED
    return SessionFailure.SESSION_FAILED
