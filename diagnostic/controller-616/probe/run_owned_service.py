from __future__ import annotations

import json
import os
import pwd
import select
import signal
import socket
import struct
import sys
import tempfile
import time
from pathlib import Path

from process_owner import STARTED, command, command_deadline

PROPERTIES: dict[str, str] = {
    "Type": "exec",
    "Restart": "no",
    "KillMode": "process",
    "OOMPolicy": "continue",
    "PrivateTmp": "no",
    "ManagedOOMMemoryPressure": "auto",
    "ManagedOOMSwap": "auto",
    "NotifyAccess": "none",
    "RemainAfterExit": "yes",
}
PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


def parse_properties(data: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in data.decode("utf-8", errors="strict").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in result:
            raise ValueError("invalid_unit_properties")
        result[key] = value
    return result


def inspect(unit: str) -> dict[str, str]:
    names = [
        *PROPERTIES,
        "Description",
        "InvocationID",
        "MainPID",
        "ControlGroup",
        "LoadState",
        "ActiveState",
        "SubState",
        "ExecMainCode",
        "ExecMainStatus",
        "Result",
    ]
    return parse_properties(
        command(
            ["/usr/bin/systemctl", "show", "--property=" + ",".join(names), "--", unit],
            cleanup=True,
        )
    )


def identify(values: dict[str, str], token: str, invocation: str | None) -> str:
    actual = values.get("InvocationID", "")
    if (
        values.get("Description") != token
        or len(actual) != 32
        or any(char not in "0123456789abcdef" for char in actual)
        or (invocation is not None and actual != invocation)
    ):
        raise RuntimeError("unit_identity_unconfirmed")
    return actual


def validate_gate(values: dict[str, str], unit: str, pid: int) -> Path:
    if any(values.get(key) != value for key, value in PROPERTIES.items()):
        raise RuntimeError("unit_properties_mismatch")
    expected = "/system.slice/" + unit
    if (
        values.get("ControlGroup") != expected
        or values.get("MainPID") != str(pid)
        or values.get("ActiveState") != "active"
        or Path(f"/proc/{pid}/cgroup").read_text() != f"0::{expected}\n"
    ):
        raise RuntimeError("unit_process_identity_mismatch")
    return Path("/sys/fs/cgroup") / expected.lstrip("/")


def empty(cgroup: Path) -> bool:
    try:
        values = dict(
            line.split() for line in (cgroup / "cgroup.events").read_text().splitlines()
        )
    except FileNotFoundError:
        return not cgroup.exists()
    if values.get("populated") not in {"0", "1"}:
        raise RuntimeError("cgroup_state_unknown")
    return values["populated"] == "0"


def groups_absent(root: Path) -> None:
    path = root / "groups"
    if not path.exists():
        return
    data = path.read_bytes()
    if len(data) > 65536:
        raise RuntimeError("group_inventory_bound")
    for item in data.splitlines():
        if not item.isdigit() or int(item) <= 1:
            raise RuntimeError("invalid_group_inventory")
        try:
            os.killpg(int(item), 0)
        except ProcessLookupError:
            continue
        raise RuntimeError("owned_group_not_absent")


def cleanup(unit: str, root: Path, invocation: str | None) -> None:
    values = inspect(unit)
    invocation = identify(values, str(root), invocation)
    expected = Path("/sys/fs/cgroup/system.slice") / unit
    if values.get("ControlGroup") not in {"", "/system.slice/" + unit}:
        raise RuntimeError("unit_cgroup_changed")
    if not empty(expected):
        identify(inspect(unit), str(root), invocation)
        command(
            [
                "/usr/bin/systemctl",
                "kill",
                "--signal=KILL",
                "--kill-whom=all",
                "--",
                unit,
            ],
            cleanup=True,
        )
    identify(inspect(unit), str(root), invocation)
    command(["/usr/bin/systemctl", "stop", "--", unit], cleanup=True)
    if not empty(expected):
        raise RuntimeError("unit_cgroup_not_empty")
    groups_absent(root)
    values = inspect(unit)
    if values.get("LoadState") != "not-found":
        identify(values, str(root), invocation)
        if values.get("ActiveState") != "failed":
            raise RuntimeError("unit_not_released")
        command(["/usr/bin/systemctl", "reset-failed", "--", unit], cleanup=True)
    if inspect(unit).get("LoadState") != "not-found":
        raise RuntimeError("unit_not_released")


def terminal_result(unit: str, root: Path, invocation: str) -> dict[str, str]:
    deadline = min(STARTED + 120.0, time.monotonic() + 5.0)
    with command_deadline(deadline):
        while time.monotonic() < deadline:
            values = inspect(unit)
            identify(values, str(root), invocation)
            if values.get("SubState") in {"exited", "dead", "failed"}:
                return values
    raise TimeoutError("service_result_deadline")


def successful(values: dict[str, str]) -> bool:
    return (
        values.get("ExecMainCode") == "1"
        and values.get("ExecMainStatus") == "0"
        and values.get("Result") == "success"
    )


def export_evidence(root: Path, archive: Path) -> None:
    for name in (
        "identity.json",
        "result.json",
        "wrapper.json",
        "stdout",
        "stderr",
        "groups",
    ):
        source = root / name
        if source.exists():
            with source.open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise RuntimeError("service_evidence_bound")
            (archive / name).write_bytes(data)


def interrupted(signum: int, frame: object) -> None:
    raise InterruptedError(f"interrupted:{signum}")


def main() -> None:
    if sys.platform != "linux" or os.geteuid() != 0 or len(sys.argv) != 3:
        raise ValueError("linux_root_checkout_evidence_required")
    uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
    if uid <= 0 or gid <= 0 or pwd.getpwuid(uid).pw_gid != gid:
        raise ValueError("ordinary_runner_identity")
    checkout = Path(sys.argv[1]).resolve(strict=True)
    evidence = Path(sys.argv[2]).absolute()
    if evidence.parent != Path("/", "tmp") or not evidence.name.startswith(
        "kora616-evidence-"
    ):
        raise ValueError("fresh_evidence_root_required")
    os.umask(0o077)
    archive = evidence.with_name(evidence.name + "-service")
    archive.mkdir(mode=0o700)
    root = Path(tempfile.mkdtemp(prefix="kora616-service-", dir="/tmp"))
    unit = root.name + ".service"
    source = Path(__file__).resolve().parent
    invocation: str | None = None
    primary: str = "not_started"
    cleaned = False
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(root / "gate"))
            listener.listen(1)
            argv = [
                "/usr/bin/systemd-run",
                "--quiet",
                "--unit=" + unit,
                "--working-directory=" + str(checkout),
                "--property=Description=" + str(root),
                "--property=StandardOutput=file:" + str(root / "stdout"),
                "--property=StandardError=file:" + str(root / "stderr"),
                "--property=RuntimeMaxSec="
                + str(max(0.001, 120.0 - (time.monotonic() - STARTED))),
                "--property=TimeoutStopSec=5s",
                *[
                    "--property=" + key + "=" + value
                    for key, value in PROPERTIES.items()
                ],
                "--setenv=PATH=" + PATH,
                "--setenv=SUDO_UID=" + str(uid),
                "--setenv=SUDO_GID=" + str(gid),
                "--setenv=KORA616_STARTED=" + str(STARTED),
                "--setenv=KORA616_GROUPS=" + str(root / "groups"),
                "--setenv=PYTHONDONTWRITEBYTECODE=1",
                "--",
                "/usr/bin/python3",
                "-B",
                str(source / "service_entry.py"),
                str(checkout),
                str(evidence),
                str(root),
            ]
            command(argv)
            listener.settimeout(min(20.0, max(0.0, STARTED + 120.0 - time.monotonic())))
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(
                    min(20.0, max(0.0, STARTED + 120.0 - time.monotonic()))
                )
                pid, peer_uid, peer_gid = struct.unpack(
                    "3i",
                    connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
                )
                if peer_uid != 0 or peer_gid != 0 or connection.recv(1) != b"R":
                    raise RuntimeError("service_peer_mismatch")
                values = inspect(unit)
                invocation = identify(values, str(root), None)
                validate_gate(values, unit, pid)
                (root / "identity.json").write_text(json.dumps(values) + "\n")
                descriptor = os.pidfd_open(pid)
                try:
                    primary = "activated"
                    connection.sendall(b"G")
                    connection.settimeout(max(0.0, STARTED + 120.0 - time.monotonic()))
                    status = connection.recv(1)
                    primary = {b"0": "complete", b"1": "failed"}.get(
                        status, "disconnected"
                    )
                    if not select.select([descriptor], [], [], 5.0)[0]:
                        raise TimeoutError("service_reap_deadline")
                finally:
                    os.close(descriptor)
                values = terminal_result(unit, root, invocation)
                (root / "result.json").write_text(json.dumps(values) + "\n")
                if not successful(values):
                    primary = "unit_failed"
                    raise RuntimeError("service_exit_failed")
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            with command_deadline(time.monotonic() + 20.0):
                cleanup(unit, root, invocation)
            cleaned = True
        finally:
            result = {
                "primary": primary,
                "cleanup": "complete" if cleaned else "unconfirmed",
                "service_evidence": str(root),
            }
            (root / "wrapper.json").write_text(json.dumps(result) + "\n")
            sys.stdout.write(json.dumps(result) + "\n")
            export_evidence(root, archive)
            if cleaned:
                for path in root.iterdir():
                    path.unlink()
                root.rmdir()
    if primary != "complete":
        raise RuntimeError("coordinator_failed")


if __name__ == "__main__":
    main()
