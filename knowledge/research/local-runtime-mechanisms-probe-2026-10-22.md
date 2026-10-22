# Research: the local runtime's OS mechanisms on GitHub's Linux and macOS runners

Checked on **2026-10-22** on GitHub-hosted runners. The probe script `probe/probe_local_runtime.py` and its workflow `.github/workflows/probe-local-runtime.yml` ran on branch `probe/local-runtime-mechanisms` of the fork `klukashka/kora`, at commit `6e26c5b62ee72777bb22ba130a25c8892b2ca369` (runs `39736590301` and `39736834054`), with CPython 3.11, standard library and `ctypes` only:

- `ubuntu-24.04`: Linux 7.0.0-1017-azure, x86_64, glibc 2.39, CPython 3.11.17, systemd user manager present;
- `macos-15`: macOS 15.7.9, arm64, CPython 3.11.9.

The local-runner probe of [2026-10-19](local-runner-adoption-probe-2026-10-19.md) ran on macOS only. This probe checks the mechanisms that the implementation plan of issue #184 relies on, on the CI machines that will run its tests.

## Linux

| mechanism | observed |
| --- | --- |
| `os.pidfd_open`, `signal.pidfd_send_signal` | work; `os.waitid(P_PIDFD, …, WEXITED \| WNOWAIT)` reports the exit status and leaves the zombie (`/proc/<pid>/stat` state `Z`) |
| `PIDFD_SIGNAL_PROCESS_GROUP` (flag `1 << 2`) | supported |
| `SO_PEERCRED` and `SO_PEERPIDFD` (option 77) | both work on a Unix socket pair; an accepted listening socket was not probed |
| `openat2` with `RESOLVE_BENEATH \| RESOLVE_NO_SYMLINKS` (syscall 437) | a plain path opens; a path through a symlinked directory fails with `ELOOP`; `..` fails with `EXDEV` |
| `renameat2(RENAME_NOREPLACE)` | refuses an existing target with `EEXIST` |
| `prctl(PR_SET_CHILD_SUBREAPER)` | an orphaned grandchild is reparented to the subreaper and listed in `/proc/self/task/<tid>/children` |
| process-group scan of `/proc/*/stat` | finds a group's members, with a killed leader held unreaped in state `Z`; the command name must be split at the last `)` |
| user manager | `/run/user/1001` and its `systemd/private` socket exist and `Linger=yes` before any preparation; `KillUserProcesses` is `false` (read through `busctl`; `loginctl show` is not a command) |
| `systemd-run --user --scope --collect -p Delegate=yes` | starts a scope under `user@1001.service/app.slice`; `cgroup.kill` is writable and a child cgroup can be created |
| unprivileged user and pid namespaces (`unshare --user --pid --fork`) | allowed |
| `fdatasync` of a 512-byte append | median 0.26 ms, p95 0.46 ms |
| host fixtures with `sudo` | a 16 MiB `tmpfs` mounts and fills to `ENOSPC`; a root-owned 0700 directory is created; `loginctl enable-linger` succeeds |

## macOS

| mechanism | observed |
| --- | --- |
| `LOCAL_PEERPID` and `LOCAL_PEERCRED` (`SOL_LOCAL`) | both work; `xucred` version 0 with the peer uid |
| `os.waitid` | **absent** in CPython 3.11 on macOS (both the runner's 3.11.9 and a local 3.11.17) |
| `kqueue` `EVFILT_PROC` with `NOTE_EXIT \| NOTE_EXITSTATUS` | fires on exit without reaping; registering after the child has exited fails with `ESRCH` |
| `renamex_np(RENAME_EXCL)` | refuses an existing target with `EEXIST` |
| a killed group leader held unreaped | `proc_listpids(PROC_PGRP_ONLY, pgid)` still lists it; `proc_pidinfo(PROC_PIDTBSDINFO)` on it fails with `ESRCH`; `killpg(pgid, 0)` succeeds while another member lives and fails with `EPERM`, not `ESRCH`, once only the zombie leader is left |
| `F_FULLFSYNC` of a 512-byte append | median 0.81 ms, maximum 12.4 ms (4 ms median on a local MacBook Pro) |
| `confstr(_CS_DARWIN_USER_TEMP_DIR)` | a 50-byte path; the planned socket path is 70 bytes, within the 104-byte `sun_path` |
| `ioreg -rd1 -c IOPlatformExpertDevice` | returns `IOPlatformUUID` in about 15 ms |
| host fixtures with `sudo` | a 16 MiB APFS RAM disk created with `hdiutil` fills to `ENOSPC`; a `root:wheel` 0700 directory is created |

## Consequences for the implementation plan of #184

- **Exit detection on macOS** cannot use `os.waitid` with `WNOWAIT`. The `kqueue` event with `NOTE_EXITSTATUS` carries the exit status in its data without reaping, so the reaper reads the status from the event and reaps once when it releases the leader.
- **Group emptiness on macOS** cannot use `killpg(pgid, 0)`, which reports `EPERM` for a group holding only a zombie leader. The plan's rule holds: list the group with `proc_listpids` and count a member whose `proc_pidinfo` fails with `ESRCH` as not live.
- **A `kevent` registration that fails with `ESRCH`** must be followed by an immediate status check, as the plan states.
- **Linux mechanisms** all behave as the plan assumes on the CI kernel; `openat2`'s `EXDEV` and `ELOOP` are the refusals to map.
- **The CI host fixtures** the plan needs, a small filesystem and a foreign-owned directory, can be prepared on both runners; the Linux runner already lingers.
- **Unprivileged user namespaces** are available on the Linux runner, so the different-pid-namespace leg of `test_process_exit_evidence_requires_same_host_and_pid_namespace` can also run against a real namespace, in addition to the injected identity source.
