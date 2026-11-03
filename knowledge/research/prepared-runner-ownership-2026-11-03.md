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
