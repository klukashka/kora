from __future__ import annotations

import json
import os
import select
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from process_owner import command_deadline, group_absent, settle

LIMIT = 65536


def record(encoded: bytes) -> dict[str, object]:
    value: object = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError("collector_record")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise ValueError("collector_record_keys")
    return {key: item for key, item in mapping.items() if isinstance(key, str)}


@dataclass(frozen=True)
class Collected:
    prepared: dict[str, object]
    completed: dict[str, object]
    stdout: bytes
    stderr: bytes


def collect(
    process: subprocess.Popen[bytes],
    deadline: float,
    activate: Callable[[int], None],
    observe: Callable[[int], None],
    preserve: Callable[[bytes, bytes], None],
) -> Collected:
    if process.stdout is None or process.stderr is None or process.stdin is None:
        raise ValueError("collector_pipes_required")
    output = bytearray()
    errors = bytearray()
    prepared: dict[str, object] | None = None
    pid: int | None = None
    streams = [process.stdout, process.stderr]
    try:
        while streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("collector_deadline")
            readable, _, _ = select.select(streams, [], [], min(0.05, remaining))
            for stream in readable:
                chunk = os.read(stream.fileno(), 4096)
                if not chunk:
                    streams.remove(stream)
                    continue
                target = output if stream is process.stdout else errors
                target.extend(chunk)
                if len(target) > LIMIT:
                    raise RuntimeError("collector_output_limit")
            if prepared is None and b"\n" in output:
                prepared = record(bytes(output).split(b"\n", 1)[0])
                child_pid = prepared.get("child_pid")
                if (
                    prepared.get("phase") != "prepared"
                    or prepared.get("mode") != "blocked"
                    or prepared.get("activation_required") is not True
                    or type(child_pid) is not int
                    or child_pid <= 1
                    or os.getpgid(child_pid) != process.pid
                ):
                    raise RuntimeError("collector_prepared_identity")
                pid = child_pid
                with command_deadline(deadline):
                    activate(pid)
                if time.monotonic() >= deadline:
                    raise TimeoutError("collector_activation_deadline")
                process.stdin.write(b"G")
                process.stdin.flush()
            if pid is not None:
                observe(pid)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("collector_deadline")
        code = process.wait(timeout=remaining)
        if code != 0 or errors or prepared is None:
            raise RuntimeError("collector_failed")
        lines = bytes(output).splitlines()
        if len(lines) != 2:
            raise RuntimeError("collector_records")
        completed = record(lines[1])
        if (
            completed.get("phase") != "reaped"
            or completed.get("mode") != "blocked"
            or type(completed.get("exit_code")) is not int
            or completed.get("exit_code") != 1
            or completed.get("ready") is not False
            or completed.get("normal_unwind") is not False
            or completed.get("parent_kill") is not False
        ):
            raise RuntimeError("collector_completion")
        if not group_absent(process):
            raise RuntimeError("collector_group_survived")
        return Collected(prepared, completed, bytes(output), bytes(errors))
    finally:
        try:
            settle(process)
        finally:
            try:
                preserve(bytes(output), bytes(errors))
            finally:
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()
