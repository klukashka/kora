# Linux quota and blocked-sync checks for #616

Status: prepared prerequisite artifacts, not production implementation. No privileged Linux run, publication or quota changes are authorized by preparing these files. Baseline d2f14e3, with the owner-approved #615 contract still local. The five-second native sync deadline and all existing CI/test deadlines remain unchanged.

## Linux quota

Files: run_linux.py, process_owner.py, quota_linux.c, quota_linux and write_quota.py. A matching manifest is mandatory. Compile against the actual target libc/architecture with `cc -D_DEFAULT_SOURCE -Wall -Wextra -Werror -std=c11 quota_linux.c -o quota_linux`; regenerate and review hashes if rebuilding. The existing local Docker image was used only without network, capabilities or host devices to compile the helper and check ordinary-file behavior. It is not a host-fixture run.

The root orchestrator creates its own 128 MiB image, ext4 internal user quotas and exact loop/mount binding. Mount options: usrquota,nodev,nosuid,noexec. A native quotactl helper sets and reads back 8 MiB and 16 MiB limits; Linux quota block limits are converted from KiB to bytes. The ordinary uid writes the actual file; required controls are successful initial write/fdatasync, exact EDQUOT with at least 16 MiB free filesystem space, and successful additional write/fdatasync after quota increase. Sync errno is independent of write errno. A missing feature, tool or wrong errno is failure, never a skip.

Invoke only after separate privileged authorization on a Linux host with python3, C runtime, e2fsprogs and util-linux: `sudo /usr/bin/python3 /absolute/reviewed/probe/run_linux.py`. No package installation or new Linux workflow is included. SUDO_UID/GID must identify the ordinary user, not root. Do not run it in a privileged Docker container against the shared Docker VM's devices.

Cleanup rechecks exact backing image and mount major:minor, unmounts only its own mount, detaches only its loop, verifies both absent, then removes the owned root. A failed or unresolved child/mount/device preserves artifacts and reports failure. No lazy/force unmount or broad deletion. The 120-second normal-work budget (cleanup excluded), 20-second individual cleanup commands and five-second child reap are diagnostic harness bounds, not replacement product deadlines.

## Native blocked sync: experiment recipe

sync_child.py performs one real regular-file write and fdatasync on a dedicated thread, reports a pending-call marker with native TID, observes the unchanged five-second deadline independently, then allows at most five seconds for fixture settlement. It never calls a substitute process-exit API. A marker before fdatasync is not proof that the syscall was entered; a successful ordinary-file run is only the positive control.

The prepared run_blocked_sync.py controller implements the following procedure, but has not executed with native privileges. Run it only after separate authorization on a disposable Linux host:

1. Allocate a new owned image/loop, create an ext4 filesystem and a device-mapper target with a unique name/UUID. Initially use a linear target; no existing host mapper/device may be selected.
2. Mount the owned target; perform the ordinary write/fdatasync control as the runner uid. Before every table/mount/cleanup operation revalidate the mapper UUID, underlying loop backing file and major:minor binding.
3. Suspend/reload/resume only that target to a nine-argument dm-delay table with reads and writes undelayed and flush delay 6000 milliseconds: `0 <sectors> delay <owned-loop> 0 0 <owned-loop> 0 0 <owned-loop> 0 6000`. This changes the stimulus, never the five-second product deadline. The required native-operation/cleanup outer deadlines are unchanged. If multiple barriers accumulate beyond them, the case fails; do not extend timeouts.
4. Run sync_child.py under external syscall observation (strace on the disposable host) and collect the exact fdatasync entry/return for the reported TID plus independently timestamped watchdog output. Confirm that fdatasync remains pending at five seconds while the controller runs. If flush is optimized away or another operation absorbs the delay, report the fixture inconclusive.
5. A separate dm-error stimulus may establish EIO, not EDQUOT. It needs its own deterministic operation-boundary controller before use; do not switch targets at guessed sleeps. Restore the owned linear mapping only after the tested process is settled, then unmount/remove mapper/detach loop with ownership checks and existing cleanup bounds. If safe restoration requires a blocked suspend, preserve and report; do not force removal.

This controller is executable but remains unqualified by native execution. Deterministic production crash barriers remain unresolved. At the five-second observation it also samples /proc/self/task/<tid>/syscall and requires the native fdatasync number compiled for the target architecture; blocked-close or pre-call scheduling cannot satisfy this check. The five seconds begin at the pre-call marker, not an externally proven syscall-entry instant. A SIGSTOP of the entire process cannot prove watchdog independence. No native macOS blocked-F_FULLFSYNC fixture has been established.

Primary mechanism: https://docs.kernel.org/admin-guide/device-mapper/delay.html .

## Forced exit

forced_exit_case.py is intentionally bound by direct import to the actual approved `kora.plugins.runtime.local.supervisor.abort_startup`. That function does not exist on inspected master, so executing this case must currently fail prerequisite validation; its missing import is not a passing exit result. The file contains no os._exit, self-kill or native-wrapper alternative. The approved exception remains restricted to the production helper, whose implementation is still unauthorized.

The case prepares two actually full pipes, starts two daemon writers, verifies no writer completes over five seconds, and either releases and joins both writers (control) or calls the actual helper (abort). This qualifies a blocked-write exit path only, not blocked filesystem sync, production diagnostics integration, the startup controller's deadline trigger or journal durability.

The prepared run_forced_exit.py parent imports and validate the real helper before spawning, executes both modes as owned subprocesses, requires the control's explicit writers_closed result, requires the abort's fixed EXIT_FAILED and absence of any success/readiness marker, and records actual process disappearance rather than an exit request. A mere nonzero exit, missing import, syntax error or externally killed child is not success. Parent timeout/cleanup retains existing bounds. Integrating the real diagnostics bootstrap and exercising the actual startup-controller trigger remain required with production implementation; the second pipe is not evidence for that integration.

Native kernel waits may delay process disappearance even when Python cleanup is bypassed. That result remains failed/unverified under existing acceptance, never silently relabelled passed. Each platform needs its own result. No forced-exit outcome has been executed or claimed here.

The parent also rejects unexpected stderr for the expected failure status, so EXIT_FAILED=1 cannot turn an ordinary Python traceback into an abort pass. The parent records deadline-to-reap latency separately; this pipe-only preparation does not qualify the production sync deadline or startup-controller trigger. No privileged run of either Linux controller is authorized yet.

## Process-group cleanup repair

After owner approval, command() now registers UNCONFIRMED_GROUPS as the first exception-handler action, before kill/reap, and removes the marker only after confirmed group absence. Independent performance/lifecycle source reassessment closed the scoped finding; cleanup guards remain ahead of device operations. The affected real single-SIGINT child kill/reap test passed in a nonprivileged Linux container. Repeated interruption was assessed from source, not experimentally reproduced. Native Linux quota, blocked sync and actual forced exit remain unverified and require separate execution authorization. All deadlines and safety checks remain unchanged.
