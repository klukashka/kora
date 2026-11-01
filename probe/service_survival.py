import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid


def command(arguments: list[str]) -> str:
    result = subprocess.run(
        arguments, capture_output=True, text=True, timeout=10, check=False
    )
    if result.returncode:
        print(
            json.dumps(
                {
                    "command": arguments,
                    "stderr": result.stderr,
                    "exit": result.returncode,
                }
            ),
            flush=True,
        )
        result.check_returncode()
    return result.stdout.strip()


def contact(path: Path, message: bytes = b"ping") -> int:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1)
        client.connect(str(path))
        client.sendall(message)
        return int(client.recv(64))


def child(path: Path) -> None:
    os.umask(0o077)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(4)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            listener.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                return
            with connection:
                connection.settimeout(1)
                message = connection.recv(16)
                connection.sendall(str(os.getpid()).encode())
            if message == b"quit":
                return


def worker(path: Path) -> None:
    signal.alarm(60)
    if not path.exists():
        subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).resolve()), "child", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    while True:
        signal.pause()


def ready(path: Path, control: list[str], unit: str) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        command([*control, "show", "--property=ActiveState", "--", unit])
        try:
            return contact(path)
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError):
            continue
    raise TimeoutError("runner socket did not become ready")


def probe() -> None:
    uid = os.getuid()
    login = "org.freedesktop.login1"
    manager = "/org/freedesktop/login1"
    bus = ["/usr/bin/busctl", "--system", "get-property", login]
    kill = command([*bus, manager, f"{login}.Manager", "KillUserProcesses"])
    linger = command([*bus, f"{manager}/user/_{uid}", f"{login}.User", "Linger"])
    if kill != "b false" or linger != "b true":
        raise RuntimeError(f"unexpected host configuration: {kill=}, {linger=}")
    print(json.dumps({"kill_user_processes": kill, "linger": linger}), flush=True)
    run_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="kora316-", dir="/tmp") as directory:
        root = Path(directory)
        for mode in ("restart", "session"):
            unit = f"kora316-{run_id}-{mode}.service"
            path = root / mode
            control = (
                ["/usr/bin/systemctl", "--user"]
                if mode == "restart"
                else ["sudo", "-n", "/usr/bin/systemctl"]
            )
            launch = (
                ["/usr/bin/systemd-run", "--user"]
                if mode == "restart"
                else [
                    "sudo",
                    "-n",
                    "/usr/bin/systemd-run",
                    f"--uid={uid}",
                    "--property=PAMName=login",
                ]
            )
            try:
                command(
                    [
                        *launch,
                        f"--unit={unit}",
                        f"--property=KillMode={'process' if mode == 'restart' else 'mixed'}",
                        "--property=RuntimeMaxSec=60s",
                        "--property=TimeoutStopSec=5s",
                        "--property=PrivateTmp=no",
                        "--property=OOMPolicy=continue",
                        "--",
                        sys.executable,
                        "-I",
                        str(Path(__file__).resolve()),
                        "worker",
                        str(path),
                    ]
                )
                before = ready(path, control, unit)
                cgroup = Path(f"/proc/{before}/cgroup").read_text()
                if mode == "session" and "session-" not in cgroup:
                    raise RuntimeError(f"PAM launch did not create a session: {cgroup}")
                session = next(
                    (
                        part.removeprefix("session-").removesuffix(".scope")
                        for part in cgroup.strip().split("/")
                        if part.startswith("session-") and part.endswith(".scope")
                    ),
                    None,
                )
                if session is not None:
                    print(
                        json.dumps(
                            {
                                "session_before": command(
                                    [
                                        "/usr/bin/loginctl",
                                        "show-session",
                                        session,
                                        "--property=State",
                                        "--property=Scope",
                                        "--property=Leader",
                                    ]
                                )
                            }
                        ),
                        flush=True,
                    )
                print(
                    json.dumps(
                        {
                            "unit_properties": command(
                                [
                                    *control,
                                    "show",
                                    "--property=ControlGroup",
                                    "--property=MainPID",
                                    "--property=KillMode",
                                    "--property=PrivateTmp",
                                    "--property=OOMPolicy",
                                    "--property=PAMName",
                                    "--",
                                    unit,
                                ]
                            )
                        }
                    ),
                    flush=True,
                )
                operation = "restart" if mode == "restart" else "stop"
                command([*control, operation, "--", unit])
                if session is not None:
                    state = command(
                        [
                            "/usr/bin/loginctl",
                            "show-session",
                            session,
                            "--property=State",
                            "--value",
                        ]
                    )
                    print(json.dumps({"session_after": state}), flush=True)
                    if state != "closing":
                        raise RuntimeError("PAM session did not close")
                after = contact(path)
                if before != after:
                    raise RuntimeError("runner identity changed")
                print(
                    json.dumps(
                        {"mode": mode, "pid": after, "cgroup": cgroup, "survived": True}
                    ),
                    flush=True,
                )
                contact(path, b"quit")
            finally:
                try:
                    contact(path, b"quit")
                except (FileNotFoundError, ConnectionRefusedError):
                    pass
                finally:
                    try:
                        result = subprocess.run(
                            [
                                *control,
                                "kill",
                                "--signal=KILL",
                                "--kill-whom=all",
                                "--",
                                unit,
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            check=False,
                        )
                        print(
                            json.dumps(
                                {"cleanup_unit": unit, "kill_exit": result.returncode}
                            ),
                            flush=True,
                        )
                    finally:
                        command([*control, "stop", "--", unit])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("probe", "worker", "child"))
    parser.add_argument("path", nargs="?")
    arguments = parser.parse_args()
    if arguments.mode == "probe":
        probe()
    elif arguments.mode == "worker":
        worker(Path(arguments.path))
    else:
        child(Path(arguments.path))


if __name__ == "__main__":
    main()
