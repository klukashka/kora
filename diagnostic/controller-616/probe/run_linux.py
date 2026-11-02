from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from process_owner import UNCONFIRMED_GROUPS, UNSETTLED_CHILDREN, command


def settled() -> None:
    if UNCONFIRMED_GROUPS:
        raise RuntimeError("unconfirmed_group_preserve_fixture")
    if any(process.poll() is None for process in UNSETTLED_CHILDREN):
        raise RuntimeError("unsettled_child_preserve_fixture")


def loop_for(image: Path) -> str | None:
    settled()
    result = json.loads(command(["losetup", "--list", "--json", "--output", "NAME,BACK-FILE"], cleanup=True))
    matches = [entry["name"] for entry in result["loopdevices"] if entry.get("back-file") == str(image)]
    if len(matches) > 1 or any(not re.fullmatch(r"/dev/loop[0-9]+", name) for name in matches):
        raise RuntimeError("ambiguous_loop")
    return matches[0] if matches else None


def mounted_device(mount: Path) -> str | None:
    matches = [line.split()[2] for line in Path("/proc/self/mountinfo").read_text().splitlines() if line.split()[4] == str(mount)]
    if len(matches) > 1:
        raise RuntimeError("ambiguous_mount")
    return matches[0] if matches else None


def verify_mount(mount: Path, device: str) -> None:
    identifier = os.stat(device).st_rdev
    expected = f"{os.major(identifier)}:{os.minor(identifier)}"
    if mounted_device(mount) != expected:
        raise RuntimeError("mount_identity_changed")


def cleanup(root: Path, mount: Path, image: Path) -> None:
    settled()
    device = loop_for(image)
    if mounted_device(mount) is not None:
        if device is None:
            raise RuntimeError("mount_without_owned_loop")
        verify_mount(mount, device)
        command(["umount", str(mount)], cleanup=True)
    settled()
    if mounted_device(mount) is not None:
        raise RuntimeError("mount_survived")
    device = loop_for(image)
    if device is not None:
        command(["losetup", "--detach", device], cleanup=True)
    settled()
    if loop_for(image) is not None:
        raise RuntimeError("loop_survived")
    shutil.rmtree(root)


def main() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("requires_linux_sudo")
    uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
    if uid <= 0 or gid < 0:
        raise ValueError("runner_identity")
    source = Path(__file__).resolve().parent
    manifest = json.loads((source / "manifest.json").read_text())
    verified = {name: (source / name).read_bytes() for name in ("quota_linux", "write_quota.py")}
    for name, payload in verified.items():
        if hashlib.sha256(payload).hexdigest() != manifest[name]:
            raise ValueError("probe_changed")
    root = Path(tempfile.mkdtemp(prefix="kora616-quota-", dir="/tmp"))
    root.chmod(0o755)
    image, mount = root / "volume.img", root / "volume"
    results: list[object] = []
    try:
        mount.mkdir(mode=0o755)
        for name, payload in verified.items():
            with (root / name).open("xb") as target:
                target.write(payload)
            (root / name).chmod(0o755)
        with image.open("xb") as target:
            os.posix_fallocate(target.fileno(), 0, 128 * 1024 * 1024)
        command(["mkfs.ext4", "-F", "-O", "quota", "-E", "quotatype=usrquota", str(image)])
        try:
            command(["losetup", "--find", "--show", "--nooverlap", str(image)])
        finally:
            device = loop_for(image)
        if device is None:
            raise RuntimeError("loop_missing")
        command(["mount", "-t", "ext4", "-o", "usrquota,nodev,nosuid,noexec", device, str(mount)])
        verify_mount(mount, device)
        data = mount / "data"
        data.mkdir(mode=0o700)
        os.chown(data, uid, gid)
        helper = str(root / "quota_linux")
        child = str(root / "write_quota.py")
        results.append(json.loads(command([helper, "set", device, str(uid), "8388608"])))
        results.append(json.loads(command([sys.executable, child, str(data), "exhaust"], runner=(uid, gid))))
        results.append(json.loads(command([helper, "get", device, str(uid), "0"])))
        results.append(json.loads(command([helper, "set", device, str(uid), "16777216"])))
        results.append(json.loads(command([sys.executable, child, str(data), "recover"], runner=(uid, gid))))
    finally:
        cleanup(root, mount, image)
    sys.stdout.write(json.dumps({"platform": "linux", "quota_probe": results, "cleanup": "complete"}) + "\n")


if __name__ == "__main__":
    main()
