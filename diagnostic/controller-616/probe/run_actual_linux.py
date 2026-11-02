from __future__ import annotations

import hashlib
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
from itertools import islice
from pathlib import Path

from interactive import collect, record
from process_owner import STARTED, command
from run_blocked_sync import change_table, cleanup, mapper_device
from run_linux import loop_for, settled, verify_mount

SECTORS = 128 * 1024 * 1024 // 512


def native_snapshots(
    pid: int,
    group: int,
    numbers: set[int],
    samples: dict[tuple[int, str, str], tuple[int, int]],
) -> None:
    try:
        if os.getpgid(pid) != group:
            raise RuntimeError("native_child_group_changed")
        with os.scandir(Path("/proc") / str(pid) / "task") as entries:
            tasks = tuple(Path(entry.path) for entry in islice(entries, 65))
    except (ProcessLookupError, FileNotFoundError):
        return
    if len(tasks) > 64:
        raise RuntimeError("native_thread_bound")
    for task in tasks:
        try:
            syscall = (task / "syscall").read_text().split()
            if (
                not syscall
                or not syscall[0].isdecimal()
                or int(syscall[0]) not in numbers
            ):
                continue
            descriptor = int(syscall[1], 0)
            target = os.readlink(task / "fd" / str(descriptor))
        except (ProcessLookupError, FileNotFoundError):
            continue
        key = (int(task.name), syscall[0], target)
        now = time.monotonic_ns()
        first, _ = samples.get(key, (now, now))
        samples[key] = (first, now)
        if len(samples) > 128:
            raise RuntimeError("native_snapshot_bound")


def main() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("requires_linux_sudo")
    if len(sys.argv) != 3:
        raise ValueError("checkout_and_new_evidence_directory_required")
    checkout = Path(sys.argv[1]).resolve(strict=True)
    evidence = Path(sys.argv[2]).absolute()
    uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
    if uid <= 0 or gid < 0:
        raise ValueError("ordinary_runner_identity")
    for tool in ("cc", "dmsetup", "strace", "mkfs.ext4", "losetup", "mount", "umount"):
        if shutil.which(tool) is None:
            raise RuntimeError("missing_tool:" + tool)
    python = checkout / ".venv" / "bin" / "python"
    if (
        not python.is_file()
        or not (
            checkout / "tests/plugins/runtime/local/startup_native_probe.py"
        ).is_file()
    ):
        raise ValueError("prepared_checkout_required")
    if evidence.parent.resolve() != Path("/", "tmp") or not evidence.name.startswith(
        "kora616-evidence-"
    ):
        raise ValueError("fresh_evidence_root_required")
    os.umask(0o077)
    evidence.mkdir(mode=0o755)
    evidence.chmod(0o755)
    raw = evidence / "raw"
    raw.mkdir(mode=0o700)
    os.chown(raw, uid, gid)
    os.chdir(checkout)
    source = Path(__file__).resolve().parent
    native = raw / "native_ids"
    command(
        [
            "cc",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source / "native_ids.c"),
            "-o",
            str(native),
        ],
        runner=(uid, gid),
    )
    identifiers = record(command([str(native)], runner=(uid, gid)))
    if set(identifiers) != {"fsync", "fdatasync", "write"} or not all(
        type(value) is int and value >= 0 for value in identifiers.values()
    ):
        raise ValueError("native_syscall_identifiers")
    numbers = {value for value in identifiers.values() if type(value) is int}
    fingerprints = {
        str(path.relative_to(checkout)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (checkout / "src", checkout / "tests")
        for path in sorted(folder.rglob("*.py"))
    }
    fingerprints.update(
        {
            "coordinator/" + path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(source.glob("*.py"))
        }
    )
    for name in ("pyproject.toml", "uv.lock"):
        fingerprints[name] = hashlib.sha256((checkout / name).read_bytes()).hexdigest()
    fingerprints["native_ids.c"] = hashlib.sha256(
        (source / "native_ids.c").read_bytes()
    ).hexdigest()
    fingerprints["native_ids"] = hashlib.sha256(native.read_bytes()).hexdigest()
    (evidence / "sources.json").write_text(
        json.dumps(fingerprints, sort_keys=True) + "\n"
    )
    root = Path(tempfile.mkdtemp(prefix="kora616-sync-", dir="/tmp"))
    root.chmod(0o755)
    image, mount = root / "volume.img", root / "volume"
    name, identity = root.name, "kora616-" + root.name
    try:
        mount.mkdir(mode=0o755)
        with image.open("xb") as target:
            os.posix_fallocate(target.fileno(), 0, SECTORS * 512)
        command(["mkfs.ext4", "-F", str(image)])
        command(["losetup", "--find", "--show", "--nooverlap", str(image)])
        loop = loop_for(image)
        if loop is None:
            raise RuntimeError("owned_loop_missing")
        command(
            [
                "dmsetup",
                "create",
                name,
                "--uuid",
                identity,
                "--table",
                f"0 {SECTORS} linear {loop} 0",
            ]
        )
        mapper = mapper_device(name, identity, loop, image)
        if mapper is None:
            raise RuntimeError("owned_mapper_missing")
        command(
            ["mount", "-t", "ext4", "-o", "nodev,nosuid,noexec", mapper, str(mount)]
        )
        verify_mount(mount, mapper)
        data = mount / "data"
        data.mkdir(mode=0o700)
        os.chown(data, uid, gid)
        module = "tests.plugins.runtime.local.startup_native_probe"
        baseline = command(
            [str(python), "-B", "-m", module, str(data / "normal"), "normal"],
            runner=(uid, gid),
        )
        (evidence / "normal.jsonl").write_bytes(baseline)
        baseline_records = [record(line) for line in baseline.splitlines()]
        if (
            len(baseline_records) != 2
            or baseline_records[-1].get("ready") is not True
            or baseline_records[-1].get("exit_code") != 0
        ):
            raise RuntimeError("positive_control_failed")
        trace = raw / "native.trace"
        argv = [
            "strace",
            "-f",
            "-ttt",
            "-T",
            "-yy",
            "-e",
            "trace=fsync,fdatasync,write,exit_group",
            "-o",
            str(trace),
            str(python),
            "-B",
            "-m",
            module,
            str(data / "blocked"),
            "blocked",
        ]
        remaining = 120.0 - (time.monotonic() - STARTED)
        if remaining <= 0:
            raise TimeoutError("probe_budget")
        deadline = time.monotonic() + min(20.0, remaining)
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            user=uid,
            group=gid,
            extra_groups=[],
            env={
                "PATH": os.environ["PATH"],
                "HOME": pwd.getpwuid(uid).pw_dir,
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        samples: dict[tuple[int, str, str], tuple[int, int]] = {}

        def activate(pid: int) -> None:
            status = (Path("/proc") / str(pid) / "status").read_text().splitlines()
            identities = [
                line.split()[1:] for line in status if line.startswith("Uid:")
            ]
            if identities != [[str(uid)] * 4]:
                raise RuntimeError("native_child_uid_changed")
            change_table(
                name,
                identity,
                loop,
                image,
                mount,
                f"0 {SECTORS} delay {loop} 0 0 {loop} 0 0 {loop} 0 6000",
            )

        def preserve(output: bytes, errors: bytes) -> None:
            (evidence / "blocked.jsonl").write_bytes(output)
            (evidence / "blocked.stderr").write_bytes(errors)

        try:
            collect(
                process,
                deadline,
                activate,
                lambda pid: native_snapshots(pid, process.pid, numbers, samples),
                preserve,
            )
        finally:
            (evidence / "native-snapshots.json").write_text(
                json.dumps(
                    {
                        "syscalls": identifiers,
                        "samples": [
                            {
                                "tid": tid,
                                "syscall": number,
                                "target": target,
                                "first_ns": first,
                                "last_ns": last,
                            }
                            for (tid, number, target), (first, last) in samples.items()
                        ],
                    }
                )
                + "\n"
            )
        sync_numbers = {str(identifiers["fsync"]), str(identifiers["fdatasync"])}
        if not any(
            number in sync_numbers
            and (
                target == str(data / "blocked")
                or target.startswith(str(data / "blocked") + "/")
            )
            for _, number, target in samples
        ):
            raise RuntimeError("native_sync_not_observed")
        if not trace.is_file() or trace.stat().st_size == 0:
            raise RuntimeError("native_trace_missing")
        settled()
    finally:
        cleanup(root, image, mount, name, identity)
    sys.stdout.write(
        json.dumps(
            {
                "collection": "complete",
                "cleanup": "complete",
                "qualification": "pending independent syscall and exit timing review",
                "evidence": str(evidence),
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
