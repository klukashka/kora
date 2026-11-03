# Prepared local runner ownership

## Scope

[Issue #654](https://github.com/realkarych/kora/issues/654) is the first prerequisite for #616. It preserves registration, readiness, serving and diagnostic ordering. It does not implement isolated I/O, journal recovery or a native-stall exit guarantee.

`prepare_registration` retains partial acquisition in cleanup stacks. A successful `PreparedRegistration` owns the bound socket and root/registry anchors. Successful `ControlServer` construction transfers the socket to the server, which owns subsequent rebind replacements. The holder releases root/registry only after actual server closure. Consuming the holder's socket field before cleanup prevents repeated unwinding from closing a reused descriptor.

`BoundSocket.close` attempts directory closure even when listener closure raises. The original listener error propagates when directory closure succeeds. No shared filesystem primitive, persisted format, deadline, diagnostic sink or entry-point configuration changes.

## Verification scope

Independent correctness, security and performance reviews cover the supervisor-to-server transfer and rebind/shutdown boundary. Correctness owns startup ordering and exception cleanup; security owns socket/directory identity and unchanged filesystem authority; performance owns descriptor, listener and task release. The common resource boundary justifies grouping supervisor and serving within each separate profile. Registry serialization and shared directory primitives remain unchanged; HTTP, client and frontend contracts are unaffected.

Review identified a directory leak when listener closure raises. The `finally` fix and real `EBADF` regression address it. Final source reassessment reported no remaining findings in the changed production boundary.

## Local evidence

Base and refreshed upstream/master: `a2871ff6ff07d3b692bdc06280797c8c8d2b2279`. Evidence is from an uncommitted macOS checkout, not a published PR revision. The production diff is 132 added-plus-deleted lines in supervisor and serving.

- `uv run --no-sync pytest -q tests/plugins/runtime/local tests/runner tests/architecture -m 'not host_fixture'`: 1,483 passed, 10 deselected, 58.96 seconds.
- The final test-only cleanup of an assertion helper was checked separately: `uv run --no-sync pytest -q tests/plugins/runtime/local/test_runner_preparation.py`: 11 passed, 0.38 seconds. Production inputs were unchanged from the broader run.
- `uv run --no-sync ruff check --ignore-noqa .`: passed.
- `uv run --no-sync ruff format --check .`: 994 files already formatted.
- `uv run --no-sync pyright`: zero errors, warnings and information messages.
- `git diff --check`: passed before this evidence record was added.

New real-resource regressions cover partial preparation refusal, registration refusal, cancellation under an actual held registry lock, pre-transfer cleanup, successful transfer, rebind/shutdown, repeated closure with a reused-descriptor guard, listener-close failure and failure after transfer before serving. Existing runner, lock, diagnostic and architecture tests retain their assertions and deadlines. An initial test incorrectly expected Linux's `EISDIR` on macOS; the platform-specific expected `EPERM` was corrected and the affected suite passed.

## Remaining evidence

Constructor failure before transfer is source-reviewed but has no executed constructor-failure regression. The after-transfer failure test exercises real server cleanup through a test helper, not an injected failure in production `supervise`. These distinctions remain open acceptance evidence; no mock or artificial production fault hook was introduced.

Host-fixture tests, Linux execution and complete final-head CI are unverified. Local checks do not establish merge readiness. No privileged fixtures or new remote workflows were run for this change.

The latest successful [master mutation audit](https://github.com/realkarych/kora/actions/runs/41297342266) covers only pricing and CEL paths at `6b6d2bcbba8372114cb4f2429cc8c1102fee0be1`: partial due to budget, restored continuation, 113 executed and zero reused. Its findings do not cover this runtime boundary. Two newer audit runs failed; no mutation evidence is claimed for this change.

## Published CI and recovery diagnostics

[PR #655 run 41672619634](https://github.com/realkarych/kora/actions/runs/41672619634) tested head `d20f251c9410d0190122893b2821653457ea9e2f`. All three Linux/macOS local-runtime lanes passed, including their host-fixture checks. The full run failed:

- macOS conformance exceeded the unchanged five-second wait for the `cancel_before_ack` handoff event. The event follows child report decoding and recovered-observer construction. Child stderr was discarded, so the delay's internal cause is not established.
- IR measurement shard 1 never started its tests. Interpreter acquisition remained at downloading CPython 3.11.17 until the ten-minute job limit. This does not demonstrate slow IR validation and justifies no change to measurement workloads or limits.
- macOS remainder failed to acquire a hosted runner. Missing producer results caused aggregate failures.

The approved diagnostic follow-up in [#654](https://github.com/realkarych/kora/issues/654#issuecomment-6611036001) captures the trusted recovery probe's stderr in an owned temporary file. Fixed stage names and monotonic timestamps identify acquisition/report boundaries without changing stdout framing. After existing cleanup, an exception note retains at most 8,192 stderr bytes and indicates truncation. Capture failure preserves an existing exception and adds only an errno marker; without an existing failure it propagates. The disk file itself is not a general bounded-output sink: the fixed test producer emits at most thirteen stage records plus possible exception output. Observation adds filesystem writes and is not a separate latency guarantee.

Five handoff-failure cases passed with diagnostics. All 59 tests in the five actual fixture-consumer modules passed in 64.14 seconds; all 18 no-suppression architecture tests passed. The final consumer-timeout regression additionally passed after a test-only typing/assertion-helper adjustment. Ruff lint/format and strict Pyright passed. Independent correctness, security and resource-lifetime source review identified and closed capture-error masking; no remaining finding was reported in the diagnostic boundary.

These results do not close the original conformance timeout or constructor-failure acceptance. An owned Linux soft-FD-limit failure in `kernel_source` would exercise pre-transfer cleanup, but would not prove a failure inside `ControlServer.__init__` and is not an equivalent macOS scenario. No such substitution, deadline relaxation, retry policy or acceptance waiver is made.

## Constructor invariant and executable refusal

The subsequent [implementation clarification](https://github.com/realkarych/kora/issues/654#issuecomment-6611755338) closes the source-level constructor-test gap without an artificial failure hook. `ControlServer.__init__` establishes the nonblocking listener mode required by its asynchronous accept loop as its first operation, before resource fields or executor allocation. Existing normal listeners already have this mode; `bound_socket` retains its configuration for rebind. No pathname validation or recovery policy changes. The complete production diff is now 133 added-plus-deleted lines.

`test_runner_preparation_constructor_failure_retains_ownership` closes a real prepared listener through `socket.close`, calls the real construction path and verifies EBADF plus a traceback frame inside the constructor. It checks retained holder ownership, unchanged Running, anchor release, FD/task balance and a guard descriptor surviving repeated closure. Its unexpected-success branch also closes the resulting server. The transfer/status/rebind test now starts with a blocking listener and proves constructor normalization and actual serving.

All 53 preparation, runner-process and registry-lock tests passed locally on macOS in 8.71 seconds. Full Ruff lint/format and strict Pyright passed. Independent correctness, security and resource-lifetime reviews found no remaining source defect in this delta. Linux execution and complete final-head CI remain pending; the regression exercises this real constructor failure, not every possible allocation failure.

The preceding [CI run 41686446421](https://github.com/realkarych/kora/actions/runs/41686446421) on `1d3f8d1589907ba75b5e32671302668ad32d9692` passed macOS conformance (508 tests, including cancel-before-ack in 2.843 seconds) and all six IR shards. It produced no new conformance failure evidence and does not establish the cause of the earlier timeout. Three macOS jobs failed to acquire a hosted runner; incomplete aggregates kept that run unsuccessful. No timing check or required status was waived.
