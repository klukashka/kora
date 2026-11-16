import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import uuid4

IMAGE = (
    "ubuntu:24.04@sha256:"
    "73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8"
)
COUNTERS = ("Ip:OutNoRoutes", "Ip6OutNoRoutes", "Udp:OutDatagrams", "Udp6OutDatagrams")


def network_counters() -> dict[str, int]:
    counters: dict[str, int] = {}
    lines = Path("/proc/net/snmp").read_text().splitlines()
    for header, values in zip(lines[::2], lines[1::2], strict=True):
        names = header.split()
        fields = values.split()
        assert names[0] == fields[0]
        for name, value in zip(names[1:], fields[1:], strict=True):
            key = names[0] + name
            if key in COUNTERS:
                counters[key] = int(value)
    for line in Path("/proc/net/snmp6").read_text().splitlines():
        name, value = line.split()
        if name in COUNTERS:
            counters[name] = int(value)
    return counters


def probe_outbound() -> None:
    for family, address in (
        (socket.AF_INET, ("192.0.2.1", 9)),
        (socket.AF_INET6, ("2001:db8::1", 9)),
    ):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with socket.socket(family, kind) as connection:
                try:
                    if kind == socket.SOCK_STREAM:
                        connection.connect(address)
                    else:
                        connection.sendto(b"probe", address)
                except OSError:
                    continue
                raise AssertionError("the network namespace has an outbound route")


def container_command(
    argv: list[str],
    *,
    name: str,
    writable: Path,
    environment: Mapping[str, str],
    cwd: Path,
) -> list[str]:
    roots = {
        Path(__file__).resolve().parents[4],
        Path(sys.base_prefix).resolve(),
        Path(sys.prefix).resolve(),
        Path(sys.executable).resolve().parent,
    }
    mounts = sorted(
        root
        for root in roots
        if not any(root != other and root.is_relative_to(other) for other in roots)
    )
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--tmpfs=/tmp:rw,exec,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--workdir",
        str(cwd),
    ]
    for key, value in environment.items():
        command.extend(("-e", f"{key}={value}"))
    for root in mounts:
        command.extend(("-v", f"{root}:{root}:ro"))
    command.extend(("-v", f"{writable}:{writable}:rw", IMAGE, *argv))
    return command


def run_contained(
    argv: list[str],
    *,
    writable: Path,
    environment: Mapping[str, str],
    cwd: Path,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    assert docker is not None, "Docker is required for contained Claude cases"
    name = f"kora-claude-{uuid4().hex}"
    try:
        return subprocess.run(
            container_command(
                argv, name=name, writable=writable, environment=environment, cwd=cwd
            ),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    finally:
        subprocess.run([docker, "rm", "-f", name], capture_output=True, check=False)


def counter_mapping(value: object) -> dict[str, int]:
    assert isinstance(value, Mapping)
    fields = cast("Mapping[object, object]", value)
    counters: dict[str, int] = {}
    for name in COUNTERS:
        count = fields[name]
        assert type(count) is int
        counters[name] = count
    return counters


def assert_no_outbound(report: dict[str, object]) -> None:
    network = report["network"]
    if sys.platform != "linux":
        assert network is None
        return
    assert isinstance(network, Mapping)
    fields = cast("Mapping[object, object]", network)
    before = counter_mapping(fields["before"])
    after = counter_mapping(fields["after"])
    probed = counter_mapping(fields["probed"])
    assert after == before
    assert probed["Ip:OutNoRoutes"] > after["Ip:OutNoRoutes"]
    assert probed["Ip6OutNoRoutes"] > after["Ip6OutNoRoutes"]
