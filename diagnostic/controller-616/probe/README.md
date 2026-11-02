# Actual-controller Linux diagnostic bundle

Prepared locally for kora #616. No privileged run or publication is authorized by this bundle. The actual implementation checkout is `/private/tmp/kora616-implementation`, based on master `60e88c54edd1af5503791040080c6ce47aa8e5e9` with uncommitted startup changes. This bundle must be reviewed and separately authorized before any native fixture execution.

## Contents and scope

- `run_actual_linux.py`: prepares its own 128 MiB loop/ext4/dm-delay fixture, runs the real collector as the ordinary sudo caller, activates only after its prepared acknowledgement, and retains trace/syscall snapshots outside the faulted volume. It requires an already prepared Linux checkout and environment; it does not install packages or publish code.
- `interactive.py`: bounded handshake, outputs, child identity, activation, observations and group settlement.
- `process_owner.py`: command/group ownership shared by every path, including the normal baseline; scoped absolute activation deadlines and no group signalling after cached leader reap.
- `run_linux.py` and `run_blocked_sync.py`: reused fixture identity and teardown primitives from the separately qualified prerequisite bundle. Their standalone entry points are not used here.
- `native_ids.c`: compile-time target-native syscall identifiers, never guessed host/architecture numbers.
- `protocol_child.py` and `check_interactive.py`: ordinary tooling protocol controls only. Their synthetic records are not native-runtime evidence.

The future privileged entry point takes exactly two arguments: the prepared checkout and a fresh `/tmp/kora616-evidence-*` directory. Evidence parents must resolve to `/tmp`; the root-owned evidence directory has a separate ordinary-uid raw-output child. The binary is compiled and executed only as the ordinary uid. All runtime/collector execution is ordinary-uid. Device/mount operations remain confined to the exact recorded fixture.

No invocation command is presented as authorized. Required future permission covers this exact bundle, the tested source snapshot, one owned loop/ext4/device-mapper experiment and cleanup on an available Linux host. A diagnostic branch/workflow publication would require separate permission. Existing quota or standalone-sync authorizations do not transfer automatically.

## Bounds and interpretation

The original 120-second experiment budget, 20-second command deadline and five-second reap guard remain. An absolute collector deadline applies inside all activation/identity commands, including cleanup-marked reads; it is not checked only after activation. Failure to confirm group absence blocks volume teardown. The helper does not signal a possibly reused group after its leader has been reaped.

Output is bounded to 64 KiB per collector stream; thread enumeration reads at most 65 directory entries with `os.scandir`, rejecting more than 64 threads. At most 128 syscall/descriptor tuples are retained. Each tuple records first/last observation, not proof that one syscall remained continuously pending. Full strace and its thread/descriptor relationship remain required. Trace is stored outside the faulted volume.

Successful collection means the real collector observed the expected exit/state conditions and native sync was observed on its owned root. It does **not** mean the five-second product deadline or every crash barrier passed. The output says qualification is pending independent syscall and exit-timing review. The trace must distinguish initial directory fsync from journal fdatasync, diagnostics pipe blocking, exit request, and actual disappearance. No trace-stop observer or macOS blocker is implemented here.

Fingerprints of Python source under src/tests, pyproject.toml, uv.lock, coordinator Python files, C source and the compiled target-native helper are saved before fixture creation. These are provenance snapshots, not a filesystem immutability mechanism. The future experiment requires an isolated unchanged checkout; any concurrent modification invalidates reuse of review results. `bundle.sha256` identifies the locally prepared scripts. Source fingerprints alone do not assert an installed environment matches its lockfile.

## Executed checks

On macOS/Python 3.11.17, six nonprivileged controls passed: valid handshake, foreign child identity, activation exception, bounded output overflow, stalled setup deadline and propagation of the activation deadline into a cleanup-marked blocked command. They verify parent descriptor counts and settled processes/groups.

The same controls passed in the already installed Linux arm64 Python 3.11 container image `sha256:84fa927127b46670e768371d1ad1be271d57ba93a6fbb3479045c7c6c452432f`. This run used UID/GID 65534, no network, a read-only root and source mount, no capabilities, no-new-privileges, 64 PIDs and 128 MiB memory. Only the private 16 MiB container tmpfs was executable for the compiled helper. The first run compiled successfully but could not execute on the default noexec tmpfs (exit 126); that result is retained, not relabeled passed. The corrected isolated scratch mount did not change any host or product setting.

Strict `cc -std=c11 -Wall -Wextra -Werror` compilation and target-native identifier output passed in that container: fsync 82, fdatasync 83, write 64. These are this arm64 container's identifiers; the future host helper must be compiled there again.

Strict Pyright passed for the five new/changed Python modules selected in `pyrightconfig.json`; the two unchanged imported prerequisite modules are excluded from this selected check, not claimed newly type-qualified. Ruff lint uses the repository's existing test-fixture rules via planned `tests/fixtures/startup_native_linux/` stdin paths, including its existing S603 exception for test subprocess owners. No repository lint setting changed. Local source parsing and formatting also pass.

No mounts, quota changes, device-mapper commands, native fault stimulus or privileged processes ran during these checks. Linux actual-controller forced exit, native sync timing, macOS F_FULLFSYNC blocking, sync errors and the remaining exact crash barriers remain unverified.


## Owned system service preparation (2026-11-03)

The first hosted actual-controller run failed ordinary safe-service admission before the native step. The owner approved local preparation of the service repair in issue #616 comment 6589035036. `run_owned_service.py` is the new root entry point; `service_entry.py` waits behind a root-only Unix socket gate before invoking the unchanged native coordinator. Ordinary pytest controls use the repository's existing user-service wrapper.

The new system unit uses a private ownership path, Description, InvocationID, MainPID, cgroup and explicit service properties. Every unit-directed destructive command rechecks identity. The wrapper preserves the original monotonic start across the service boundary; service activation does not grant a fresh 120-second budget. Command, reap and job limits remain 20 seconds, five seconds and five minutes. Successful collection requires both the coordinator response and the actual normal service exit status. Empty cgroup and absent recorded groups are required for confirmed cleanup; uncertainty preserves fixtures and fails.

Service stdout, stderr, identity, actual result, group inventory and wrapper outcome are exported as six fixed files, each bounded to 1 MiB, into the exact experiment-evidence path with `-service` appended. No volume is traversed. An oversized file fails export and preserves the original service directory; it is not reported as successfully uploaded. The staging shell copies this archive even after a nonzero wrapper result.

Nonprivileged controls passed on macOS and in the existing isolated Linux arm64 container: malformed/foreign identity refusal, live-group refusal and actual reap, inherited start and invalid-start refusal, abnormal-exit checker rejection, and fixed-file failure-evidence export. The existing six protocol controls also passed. Strict Pyright for the selected eight changed/new modules and scoped Ruff lint/format passed. The unchanged prerequisite imports have not been newly type-qualified.

Independent correctness, security and performance source reviews closed the exit-status and missing-artifact findings. These results do not demonstrate actual systemd activation, cancellation, foreign-unit collision, native delayed I/O or forced exit. A separately authorized publication and privileged Linux execution remain necessary. No new commit, push or privileged run has occurred.
