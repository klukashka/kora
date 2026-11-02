from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from process_owner import command
from run_linux import loop_for, mounted_device, settled, verify_mount


def mapper_device(name: str, identity: str, loop: str, image: Path) -> str | None:
    settled()
    if loop_for(image) != loop:
        raise RuntimeError("loop_backing_changed")
    path = Path('/dev/mapper') / name
    if not path.exists():
        return None
    actual = command(['dmsetup', 'info', '-c', '--noheadings', '-o', 'uuid', name], cleanup=True).decode().strip()
    if actual != identity:
        raise RuntimeError('mapper_identity_changed')
    table = command(['dmsetup', 'table', name], cleanup=True).decode().split()
    device = os.stat(loop).st_rdev
    token = f'{os.major(device)}:{os.minor(device)}'
    if len(table) not in (5, 12) or table[2] not in {'linear', 'delay'} or table[3] != token:
        raise RuntimeError('mapper_backing_changed')
    if len(table) == 12 and (table[6] != token or table[9] != token):
        raise RuntimeError('mapper_backing_changed')
    return str(path)


def change_table(name: str, identity: str, loop: str, image: Path, mount: Path, table: str, *, cleaning: bool = False) -> None:
    if mapper_device(name, identity, loop, image) is None:
        raise RuntimeError("mapper_missing")
    if mounted_device(mount) is not None:
        verify_mount(mount, str(Path("/dev/mapper") / name))
    info = command(["dmsetup", "info", name], cleanup=cleaning).decode()
    state = re.findall(r"^State:\s+(ACTIVE|SUSPENDED)\s*$", info, re.MULTILINE)
    if len(state) != 1:
        raise RuntimeError("mapper_state_unknown")
    if state[0] == "ACTIVE":
        if mapper_device(name, identity, loop, image) is None:
            raise RuntimeError("mapper_missing")
        command(["dmsetup", "suspend", name], cleanup=cleaning)
    for argv in (["dmsetup", "reload", name, "--table", table], ["dmsetup", "resume", name]):
        if mapper_device(name, identity, loop, image) is None:
            raise RuntimeError("mapper_missing")
        if mounted_device(mount) is not None:
            verify_mount(mount, str(Path("/dev/mapper") / name))
        command(argv, cleanup=cleaning)


def cleanup(root: Path, image: Path, mount: Path, name: str, identity: str) -> None:
    settled()
    loop = loop_for(image)
    if loop is None:
        if mounted_device(mount) is not None or (Path('/dev/mapper') / name).exists():
            raise RuntimeError('missing_backing_preserve_fixture')
    else:
        mapper = mapper_device(name, identity, loop, image)
        if mapper is not None:
            change_table(name, identity, loop, image, mount, f"0 {128 * 1024 * 1024 // 512} linear {loop} 0", cleaning=True)
        if mounted_device(mount) is not None:
            if mapper is None:
                raise RuntimeError('mount_without_mapper')
            verify_mount(mount, mapper)
            command(['umount', str(mount)], cleanup=True)
        settled()
        if mounted_device(mount) is not None:
            raise RuntimeError('mount_survived')
        if mapper_device(name, identity, loop, image) is not None:
            command(['dmsetup', 'remove', name], cleanup=True)
        settled()
        if (Path('/dev/mapper') / name).exists():
            raise RuntimeError('mapper_survived')
        if loop_for(image) != loop:
            raise RuntimeError('loop_identity_changed')
        command(['losetup', '--detach', loop], cleanup=True)
    if loop_for(image) is not None:
        raise RuntimeError('loop_survived')
    shutil.rmtree(root)


def main() -> None:
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('requires_linux_sudo')
    uid, gid = int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID'])
    if uid <= 0 or gid < 0:
        raise ValueError('runner_identity')
    for tool in ('dmsetup', 'strace', 'mkfs.ext4', 'losetup', 'mount', 'umount'):
        if shutil.which(tool) is None:
            raise RuntimeError('missing_tool:' + tool)
    source = Path(__file__).resolve().parent
    payload = (source / 'sync_child.py').read_bytes()
    native_payload = (source / 'native_ids').read_bytes()
    manifest = json.loads((source / 'manifest.json').read_text())
    if hashlib.sha256(payload).hexdigest() != manifest['sync_child.py'] or hashlib.sha256(native_payload).hexdigest() != manifest['native_ids']:
        raise ValueError('probe_changed')
    root = Path(tempfile.mkdtemp(prefix='kora616-sync-', dir='/tmp'))
    root.chmod(0o755)
    image, mount = root / 'volume.img', root / 'volume'
    name, identity = root.name, 'kora616-' + root.name
    results: dict[str, object] = {}
    try:
        mount.mkdir(mode=0o755)
        child = root / 'sync_child.py'
        child.write_bytes(payload)
        child.chmod(0o644)
        native = root / 'native_ids'
        native.write_bytes(native_payload)
        native.chmod(0o755)
        syscall_number = command([str(native)], runner=(uid, gid)).decode().strip()
        if not syscall_number.isdecimal():
            raise ValueError('native_syscall_number')
        with image.open('xb') as target:
            os.posix_fallocate(target.fileno(), 0, 128 * 1024 * 1024)
        command(['mkfs.ext4', '-F', str(image)])
        command(['losetup', '--find', '--show', '--nooverlap', str(image)])
        loop = loop_for(image)
        if loop is None:
            raise RuntimeError('loop_missing')
        sectors = 128 * 1024 * 1024 // 512
        command(['dmsetup', 'create', name, '--uuid', identity, '--table', f'0 {sectors} linear {loop} 0'])
        mapper = mapper_device(name, identity, loop, image)
        if mapper is None:
            raise RuntimeError('mapper_missing')
        command(['mount', '-t', 'ext4', '-o', 'nodev,nosuid,noexec', mapper, str(mount)])
        verify_mount(mount, mapper)
        data = mount / 'data'
        data.mkdir(mode=0o700)
        os.chown(data, uid, gid)
        baseline = command([sys.executable, str(child), str(data / 'baseline'), syscall_number], runner=(uid, gid))
        records = [json.loads(line) for line in baseline.splitlines()]
        if records[-1]['sync_errno'] != 0 or records[1]['five_second_observation'] != 'completed':
            raise RuntimeError('positive_control_failed')
        results['baseline'] = records
        change_table(name, identity, loop, image, mount, f'0 {sectors} delay {loop} 0 0 {loop} 0 0 {loop} 0 6000')
        mapper_device(name, identity, loop, image)
        trace = data / 'sync.trace'
        output = command(['strace', '-f', '-ttt', '-T', '-e', 'trace=fdatasync', '-o', str(trace), sys.executable, str(child), str(data / 'delayed'), syscall_number], runner=(uid, gid))
        records = [json.loads(line) for line in output.splitlines()]
        native_trace = trace.read_text()
        results['delayed'] = records
        results['trace'] = native_trace
        sys.stdout.write(json.dumps({'observations': results}) + '\n')
        sys.stdout.flush()
        tid = records[0]['tid']
        durations = re.findall(r'^\s*' + str(tid) + r'\s+\d+\.\d+\s+fdatasync\(\d+\)\s+=\s+0\s+<([0-9.]+)>', native_trace, re.MULTILINE)
        if records[1]['native_fdatasync_observed'] is not True or records[1]['five_second_observation'] != 'pending' or records[-1]['sync_errno'] != 0 or len(durations) != 1 or float(durations[0]) < 5.0:
            raise RuntimeError('blocked_sync_not_demonstrated')
    finally:
        cleanup(root, image, mount, name, identity)
    sys.stdout.write(json.dumps({'blocked_sync': 'demonstrated', 'forced_exit_exercised': False, 'cleanup': 'complete'}) + '\n')


if __name__ == '__main__':
    main()
