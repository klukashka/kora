import ctypes
import errno
import json
import os
import platform
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

RESULTS: dict[str, object] = {}


def check(name: str) -> Callable[[Callable[[], object]], None]:
    def run(function: Callable[[], object]) -> None:
        started = time.monotonic()
        try:
            RESULTS[name] = {"ok": True, "value": function()}
        except Exception as error:  # probe only: record every outcome
            RESULTS[name] = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        RESULTS[name]["seconds"] = round(time.monotonic() - started, 4)  # type: ignore[index]

    return run


def run(argv: list[str], timeout: float = 20) -> dict[str, object]:
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return {"rc": completed.returncode, "out": completed.stdout[-2000:], "err": completed.stderr[-2000:]}


def environment() -> dict[str, object]:
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "uid": os.getuid(),
        "user": os.environ.get("USER"),
        "xdg_runtime_dir": os.environ.get("XDG_RUNTIME_DIR"),
        "pgid_equals_pid": os.getpgid(0) == os.getpid(),
    }


def spawn_sleeper(seconds: str = "30", new_session: bool = True) -> int:
    return os.posix_spawn("/bin/sleep", ["sleep", seconds], dict(os.environ), setsid=new_session)


LIBC = ctypes.CDLL(None, use_errno=True)


def linux_checks() -> None:
    @check("pidfd_open_and_send_signal")
    def _() -> object:
        pid = spawn_sleeper()
        fd = os.pidfd_open(pid)
        signal.pidfd_send_signal(fd, 0)
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        state = os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOWAIT)
        zombie_state = Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0]
        os.waitid(os.P_PIDFD, fd, os.WEXITED)
        os.close(fd)
        return {"wnowait_status": state.si_status if state else None, "zombie_state_field": zombie_state}

    @check("pidfd_signal_process_group")
    def _() -> object:
        pid = spawn_sleeper()
        fd = os.pidfd_open(pid)
        flag = 1 << 2
        try:
            signal.pidfd_send_signal(fd, 0, None, flag)
            supported = True
        except OSError as error:
            supported = errno.errorcode.get(error.errno or 0, str(error.errno))
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        os.close(fd)
        return {"supported": supported, "kernel": platform.release()}

    @check("so_peerpidfd_and_peercred")
    def _() -> object:
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        cred = right.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", cred)
        try:
            peer_fd = right.getsockopt(socket.SOL_SOCKET, 77)
            pidfd = {"ok": True, "fd": peer_fd}
            os.close(peer_fd)
        except OSError as error:
            pidfd = {"ok": False, "errno": errno.errorcode.get(error.errno or 0)}
        left.close()
        right.close()
        return {"peercred": [pid, uid, gid], "own_pid": os.getpid(), "so_peerpidfd": pidfd}

    @check("openat2_resolve")
    def _() -> object:
        class OpenHow(ctypes.Structure):
            _fields_ = [("flags", ctypes.c_uint64), ("mode", ctypes.c_uint64), ("resolve", ctypes.c_uint64)]

        root = Path(tempfile.mkdtemp())
        (root / "real").mkdir()
        (root / "real" / "f").write_text("x")
        os.symlink(root / "real", root / "link")
        dirfd = os.open(root, os.O_DIRECTORY)
        results: dict[str, object] = {}
        for label, path in {"plain": b"real/f", "through_symlink": b"link/f", "dotdot": b"../x"}.items():
            how = OpenHow(os.O_RDONLY | os.O_CLOEXEC, 0, 0x08 | 0x04)
            fd = LIBC.syscall(437, dirfd, ctypes.c_char_p(path), ctypes.byref(how), ctypes.sizeof(how))
            if fd >= 0:
                os.close(fd)
                results[label] = "opened"
            else:
                results[label] = errno.errorcode.get(ctypes.get_errno())
        os.close(dirfd)
        shutil.rmtree(root)
        return results

    @check("renameat2_noreplace")
    def _() -> object:
        root = Path(tempfile.mkdtemp())
        (root / "a").write_text("a")
        (root / "b").write_text("b")
        rc = LIBC.renameat2(-100, b"" + str(root / "a").encode(), -100, str(root / "b").encode(), 1)
        result = {"rc": rc, "errno": errno.errorcode.get(ctypes.get_errno()) if rc != 0 else None}
        shutil.rmtree(root)
        return result

    @check("subreaper_adopts_orphan")
    def _() -> object:
        rc = LIBC.prctl(36, 1, 0, 0, 0)
        shell = subprocess.Popen(["/bin/sh", "-c", "sleep 30 & echo $!"], stdout=subprocess.PIPE, text=True)
        orphan = int(shell.stdout.readline())  # type: ignore[union-attr]
        shell.wait()
        time.sleep(0.2)
        parent = int(Path(f"/proc/{orphan}/stat").read_text().split(")")[1].split()[1])
        children = Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()
        os.kill(orphan, signal.SIGKILL)
        os.waitpid(orphan, 0)
        return {"prctl_rc": rc, "orphan_parent_is_us": parent == os.getpid(), "listed_in_children": str(orphan) in children}

    @check("group_membership_scan_with_held_leader")
    def _() -> object:
        leader = os.posix_spawn("/bin/sh", ["sh", "-c", "sleep 30 & echo $! > MEMBER; exec sleep 30"], dict(os.environ), setsid=True)
        time.sleep(0.3)
        member = int(Path("MEMBER").read_text())
        os.kill(leader, signal.SIGKILL)
        time.sleep(0.2)
        members = {}
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                try:
                    fields = (entry / "stat").read_text().split(")")[1].split()
                except OSError:
                    continue
                if int(fields[2]) == leader:
                    members[entry.name] = fields[0]
        os.kill(member, signal.SIGKILL)
        os.waitpid(leader, 0)
        return {"members_with_state": members, "leader": leader, "member": member}

    @check("user_manager_and_linger")
    def _() -> object:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        return {
            "runtime_dir": runtime,
            "runtime_dir_exists": Path(runtime).is_dir(),
            "systemd_private": Path(runtime, "systemd", "private").exists(),
            "linger": run(["loginctl", "show-user", str(os.getuid()), "-p", "Linger"]),
            "kill_user_processes": run(["loginctl", "show", "-p", "KillUserProcesses"]),
            "socket_dir_length": len(os.path.join(runtime, "kora", "0123456789abcdef")),
        }

    @check("systemd_run_user_scope")
    def _() -> object:
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={env['XDG_RUNTIME_DIR']}/bus")
        unit = f"kora-probe-{os.getpid()}"
        script = "cat /proc/self/cgroup; d=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); ls $d | head -40; test -w $d/cgroup.kill && echo KILL_WRITABLE; mkdir $d/leaf && echo LEAF_OK"
        completed = subprocess.run(
            ["systemd-run", "--user", "--scope", "--collect", "-p", "Delegate=yes", "--unit", unit, "/bin/sh", "-c", script],
            capture_output=True, text=True, timeout=30, env=env,
        )
        return {"rc": completed.returncode, "out": completed.stdout[-3000:], "err": completed.stderr[-2000:]}

    @check("unprivileged_user_namespace")
    def _() -> object:
        return run(["unshare", "--user", "--pid", "--fork", "/bin/true"])

    @check("fdatasync_cost")
    def _() -> object:
        with tempfile.NamedTemporaryFile(dir=os.environ.get("RUNNER_TEMP")) as handle:
            durations = []
            for _ in range(50):
                handle.write(b"x" * 512)
                handle.flush()
                started = time.monotonic()
                os.fdatasync(handle.fileno())
                durations.append(time.monotonic() - started)
        durations.sort()
        return {"median_ms": round(durations[25] * 1000, 3), "p95_ms": round(durations[47] * 1000, 3)}


def darwin_checks() -> None:
    libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)

    @check("local_peerpid_and_peercred")
    def _() -> object:
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        peer_pid = struct.unpack("i", right.getsockopt(0, 2, 4))[0]
        cred = right.getsockopt(0, 1, 76)
        version, uid = struct.unpack("Ii", cred[:8])
        left.close()
        right.close()
        return {"peer_pid": peer_pid, "own_pid": os.getpid(), "xucred_version": version, "uid": uid}

    @check("kqueue_proc_exit")
    def _() -> object:
        queue = select.kqueue()
        pid = spawn_sleeper("1")
        note = select.KQ_NOTE_EXIT | 0x04000000
        event = select.kevent(pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD | select.KQ_EV_ONESHOT, note)
        fired = queue.control([event], 1, 5)
        status = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT) if hasattr(os, "waitid") else "os.waitid unavailable"
        os.waitpid(pid, 0)
        late = spawn_sleeper("0")
        time.sleep(0.5)
        try:
            queue.control([select.kevent(late, select.KQ_FILTER_PROC, select.KQ_EV_ADD, note)], 0, 0)
            late_result = "registered"
        except OSError as error:
            late_result = errno.errorcode.get(error.errno or 0)
        os.waitpid(late, 0)
        return {"fired": len(fired), "fflags": fired[0].fflags if fired else None, "wnowait": str(status), "late_registration": late_result}

    @check("renamex_np_excl")
    def _() -> object:
        root = Path(tempfile.mkdtemp())
        (root / "a").write_text("a")
        (root / "b").write_text("b")
        rc = libsystem.renamex_np(str(root / "a").encode(), str(root / "b").encode(), 0x4)
        result = {"rc": rc, "errno": errno.errorcode.get(ctypes.get_errno()) if rc != 0 else None}
        shutil.rmtree(root)
        return result

    @check("zombie_leader_group_visibility")
    def _() -> object:
        leader = os.posix_spawn("/bin/sh", ["sh", "-c", "sleep 30 & echo $! > MEMBER; exec sleep 30"], dict(os.environ), setsid=True)
        time.sleep(0.3)
        member = int(Path("MEMBER").read_text())
        os.kill(leader, signal.SIGKILL)
        time.sleep(0.3)
        buffer = (ctypes.c_int * 64)()
        count = libsystem.proc_listpids(2, leader, buffer, ctypes.sizeof(buffer)) // ctypes.sizeof(ctypes.c_int)
        listed = [buffer[i] for i in range(count) if buffer[i] != 0]
        info = ctypes.create_string_buffer(512)
        bsd = libsystem.proc_pidinfo(leader, 3, 0, info, 512)
        bsd_errno = errno.errorcode.get(ctypes.get_errno()) if bsd <= 0 else None
        status = struct.unpack_from("I", info.raw, 4)[0] if bsd > 0 else None
        try:
            os.killpg(leader, 0)
            killpg = "succeeded"
        except OSError as error:
            killpg = errno.errorcode.get(error.errno or 0)
        os.kill(member, signal.SIGKILL)
        time.sleep(0.3)
        try:
            os.killpg(leader, 0)
            killpg_only_zombie = "succeeded"
        except OSError as error:
            killpg_only_zombie = errno.errorcode.get(error.errno or 0)
        os.waitpid(leader, 0)
        return {
            "listed": listed, "leader": leader, "member": member,
            "proc_pidinfo_bytes": bsd, "proc_pidinfo_errno": bsd_errno, "pbi_status": status,
            "killpg_with_member": killpg, "killpg_only_zombie_leader": killpg_only_zombie,
        }

    @check("f_fullfsync_cost")
    def _() -> object:
        import fcntl

        with tempfile.NamedTemporaryFile() as handle:
            durations = []
            for _ in range(20):
                handle.write(b"x" * 512)
                handle.flush()
                started = time.monotonic()
                fcntl.fcntl(handle.fileno(), fcntl.F_FULLFSYNC)
                durations.append(time.monotonic() - started)
        durations.sort()
        return {"median_ms": round(durations[10] * 1000, 3), "max_ms": round(durations[-1] * 1000, 3)}

    @check("darwin_user_temp_dir")
    def _() -> object:
        buffer = ctypes.create_string_buffer(1024)
        length = libsystem.confstr(65537, buffer, 1024)
        path = buffer.value.decode()
        return {"length": length, "socket_path_length": len(os.path.join(path, "kora", "0123456789abcdef"))}

    @check("ioreg_platform_uuid")
    def _() -> object:
        result = run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
        return {"rc": result["rc"], "has_uuid": "IOPlatformUUID" in str(result["out"])}


def main() -> None:
    RESULTS["environment"] = environment()
    if sys.platform == "linux":
        linux_checks()
    else:
        darwin_checks()
    print(json.dumps(RESULTS, indent=2, default=str))


main()
