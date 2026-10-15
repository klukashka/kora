# kora MVP design — scheduler

Status: design draft for owner review (2026-10-15, revised after the design review, see [review-2026-10-15.md](review-2026-10-15.md)). Scope: FR-6.1–6.10, FR-1.6, NFR-1, NFR-2, NFR-3, NFR-7. The scheduler is a stateless loop over the tables in [data-model.md](data-model.md); it submits work through the runtime protocol in [protocols.md](protocols.md). Owner decisions on design choices are referenced as **DD-n** ("design decision n") and recorded in [design-decisions.md](design-decisions.md).

## 1. Components and processes

| process | role | MVP deployment |
|---|---|---|
| `kora api` | HTTP API, webhook endpoints, worker endpoints, MCP endpoints, static frontend | 1..n replicas, stateless |
| `kora scheduler` | the passes below: triggers, readiness, admission, waits, timeouts, completion, clear-downstream | 1 replica in MVP; designed for n with per-row claims and a per-run lock |
| `kora worker` | one per runtime: claims attempts, prepares workspaces, submits, consumes the session event stream, enforces limits, uploads segments and artifacts | 1..n per runtime name, registered in the `worker` table; a worker has **one path to the control plane, the HTTP API**, with a service token, outbound only (DD-13, review item 3); it may run inside an adopter's network |
| `kora retention` | the retention task (section 13) | a low-priority coroutine of the scheduler process, batch-paced |
| `kora standalone` | api + scheduler + worker(local) in one process on SQLite; the worker uses the same API client over an in-process ASGI transport | development |

The split between scheduler and worker is a decision (DD-13) and follows the Airflow executor model: the scheduler decides and persists; workers perform the runtime calls and report through the API. There is no direct-SQL path for workers.

## 2. The scheduling loop

The scheduler runs the following passes continuously; each pass is a short transaction that claims rows, decides, and writes rows. No pass holds state in memory between iterations (NFR-1.2). Passes 2 to 8 operate per run under a **per-run lease** (a transaction-scoped advisory lock on `run.id` on PostgreSQL, the single writer on SQLite), so two scheduler replicas never evaluate the same run concurrently (review item 16); triggers and timeouts stay per row.

1. **Triggers.** Fire due `trigger_binding` rows (cron `next_fire_at <= now`, sensor polls due through the sensor protocol); create `run` rows with derived idempotency keys; apply the graph's `overlap` (`allow`, the default for every trigger kind: always start; `skip`: no run if one is `running` for the graph; `queue`: create `pending` and start later; DD-12). Webhooks are authenticated by the API (HMAC or bearer per trigger, DD-17) and turned into `inbound_event` rows; the trigger pass converts those addressed to a trigger binding into runs and those addressed to waits into wake-ups.
2. **Materialise.** A `pending` run gets its top-level node instances created from the pinned graph version's IR; subgraph bodies and fan-out instances are materialised lazily when the containing node becomes `ready` (NFR-3.3).
3. **Readiness.** A query, not a dirty set: `pending` instances of the run whose upstream instances are all terminal under the trigger rule (review item 1). For each: resolve inputs to `current` artifact versions, write `consumed_versions`, move to `ready` or `skipped`/`upstream_failed`. Only runs touched since the last pass (`run.updated_at`) are visited, which keeps the pass incremental (NFR-3.3).
4. **Branch and collection.** `ready` branch instances evaluate their CEL expression over consumed artifacts and mark non-chosen targets `skipped`; a fan-out instance whose body instances are all terminal collects their `collect` outputs into its `List` artifact versions and completes (there is no fan-in node).
5. **Admission.** `ready` instances that need a runtime (`agent`, `command`) become `queued` with an `attempt` row (`created`, `submission_key`), subject to concurrency limits and the namespace's daily soft cost limit (section 6); an instance held back gets an `admission` wait row rather than a field of its own. Waiting kinds (`human_input`, `wait_event`, `timer`) become `waiting` with a `wait` row and, for approvals, an `approval_request` row (section 5).
6. **Wake-ups.** `wait` rows with `wake_at <= now` or a matching `inbound_event` are resolved; the owning instance returns to `running` (sensor: evaluate `until`, else reschedule), `ready` (retry back-off) or completes (timer).
7. **Timeouts and liveness** (section 4).
8. **Completion.** Attempts in `finishing` whose artifacts are stored move their instance to `succeeded` or `failed`; retry policy is applied (new `queued` attempt with back-off wait, or terminal failure); a sub-graph instance whose iteration failed and may retry runs **clear downstream** for its body and re-materialises it with `iteration + 1` (data-model.md section 6); the run completes when no instance is non-terminal.

A manual retry from the API is the same clear-downstream transaction on the chosen node, followed by the ordinary passes. When an approval request is created (pass 5), the scheduler sums the run's usage records into `attempt_cost` on the request (DD-8).

Workers run a separate loop: claim `created` attempts of their runtime through the API (section 3), prepare the workspace, submit, stream, enforce, finalise.

## 3. At-most-once submission, leases and adoption

```
scheduler:  INSERT attempt(status=created, submission_key=sub:<att>)                       -- committed
worker:     POST /workers/{id}/claims {runtime}  → attempt(status=claimed, lease_owner, lease_epoch+1, lease_until=now+120s)
worker:     handle = runtime.submit(workload)                                               -- idempotent on submission_key
worker:     POST /attempts/{att}/transition {to: submitted, handle, lease_epoch}             -- committed
worker:     every 15s: POST /attempts/{att}/heartbeat {lease_epoch, counters} → {desired_state, lease_until}
```

- **Fencing** (review item 10). Every claim increments `lease_epoch`; every heartbeat, segment upload, artifact upload and transition carries it; the API rejects a stale epoch with `API008`. A worker whose renewal is rejected stops streaming and abandons the attempt locally; it never writes again under that epoch. The claim query excludes attempts of cancelled runs and attempts with `desired_state = cancel`.
- **Lease**: 120 s, renewed every 15 s with the heartbeat. Before the scheduler reclaims an attempt whose lease expired, it first asks the runtime `heartbeat(handle)` (and, when the worker is still registered, the worker through its registry entry); only when both are silent is the row reclaimed with a new epoch.
- A crash between claim and handle persistence leaves `status=claimed` with an expired lease. The recovery path calls `runtime.lookup(submission_key)`: a handle means the submission happened and is persisted now; `None` means it did not and the attempt returns to `created`. Either way no second session is started (FR-6.1a, NFR-2.1).
- A crash after `submitted` leaves an expired lease on a live handle. Any worker of that runtime may claim the row (new epoch) and call `runtime.adopt(handle)`, which resumes the event stream from the stored offset (`heartbeat_counters.stream_offset`) against the event file the runtime keeps in the workspace (section 4), and continues enforcement with the persisted counters. Adoption is by `submission_key` lookup when the handle is missing and by handle otherwise.
- Adoption of a runtime that does not know the handle any more yields `lost`, classified `infrastructure`; the runtime-level `infrastructure_retry` policy decides whether to resubmit, and it does not consume the node's agent attempts (NFR-2.4).
- **Cancellation delivery** (review item 15): the API sets `desired_state = cancel`; the worker reads it from the heartbeat response and calls `runtime.cancel(handle, grace)`. Two attempts of one node instance can never share a `number` (unique constraint).

## 4. Heartbeats, stall detection and timeouts

Two liveness signals are kept apart (NFR-2.3):

- **Runtime liveness**: the worker's lease (`lease_until`) and `runtime.heartbeat(handle)`. A lease that expires means the worker died; the row is re-claimable after the check in section 3. A runtime that reports the session gone means `lost`.
- **Agent-loop liveness**: `last_event_at` in `heartbeat_counters`, advanced by every normalised event. A healthy container whose stream has been silent for `stall_after` is classified `stalled`: the worker cancels the session with grace, the attempt fails with class `stalled`, and the node's retry policy decides. `stall_after` comes from the node's `limits.stall_after`, else the graph's `runtime.stall_after`, else the runtime's `default_stall_after` (15 minutes in the reference runtimes; DD-15). The window is **extended while the last event is an unanswered tool call** (a long dev-run inside one `Bash` call is not a stall) up to the node timeout; it applies in full only when the harness is between tool calls (review item 17).

The session's event stream is written by the runtime to a file in the workspace (`.kora/events.jsonl`) as the resumable source of truth; the worker tails it, so adoption after a worker crash resumes from the recorded offset. The local runtime spawns each session in its own process group tagged with the attempt id and kills orphaned groups it finds when it claims work.

Timeouts (FR-6.5): the scheduler's timeout pass compares `attempt.started_at + limits.timeout` and `run.started_at + runtime.limits.timeout` with `now`; breaches cancel with grace and fail with class `timeout`. Wait expiry is `wait.wake_at` for timers and `approval_request.expires_at`/`wait.subscription.timeout_at` for approvals and sensors; expiry applies `on_expiry` (NFR-2.5). Subgraph timeouts and budgets are checked against the subgraph instance across attempts (data-model.md section 5).

Grace handling (FR-6.7): `cancel(handle, grace)` sends the harness a termination signal, waits `grace`, then kills; artifacts the session already wrote are collected as `partial`, usage records are marked `partial` (FR-9.1), and the workspace is released after collection.

## 5. Waits hold no worker (NFR-1.3)

A waiting instance is one `wait` row and, for approvals, one `approval_request` row. Nothing else exists: no coroutine, no container, no lease. Three resolution paths:

- **Time**: `wake_at` scanned by the wake-up pass; timers and retry back-off.
- **Event**: the API writes an `inbound_event`; the wake-up pass matches it against open `wait.subscription` rows (`name` equality, then the `match` expression over payload and inputs), marks the wait `satisfied`, records `consumed_by`.
- **Poll**: sensors are polled by the scheduler at `interval` through the sensor protocol (`observe` returns an observation the node's `until` CEL expression is evaluated on); the `vcs` approval channel is polled through the approval protocol. A poll is a short call, not a held slot. Sensors that declare `supports_push` and chat channels with push notifications (post-MVP) deliver through the same `inbound_event` path.

An approval response from any channel is applied through one function: load the request, check that it is still `pending`, compare `(commit_sha, digests)` with the bound values, then transition. The channel is only a transport (FR-7.2a).

## 6. Concurrency limits (FR-6.10)

Limits are evaluated in the admission pass, in this order, each as a count over rows: global `max_running_attempts` (deployment config), per namespace (`namespace.policy.concurrency`), per graph (`runtime.concurrency.max_running_nodes`, `max_running_runs`), per runtime (`RuntimeCapabilities.max_concurrent` and NFR-7.4 configured limit), per fan-out (`max_parallel`, counted over body instances of the same fan-out instance). A `ready` instance that fails any check gets an `admission` wait row with `wake_at = now + admission_interval`; the oldest ready instance wins (FIFO by `node_instance.id`). Limits live in rows and config reloaded every pass, so they change without restart (NFR-7.4).

**Daily soft cost limit** (DD-31): `namespace.policy.daily_budget_usd`. When the namespace's estimated spend for the current day (sum of `usage_record.usd_estimate` over attempts started today, in the namespace's timezone) exceeds it, no new attempts are admitted; running ones finish. The state is visible in `/metrics` (`kora_namespace_daily_spend_usd`, `kora_namespace_budget_blocked`) and on `GET /namespaces/{ns}/cost/daily`.

## 7. Retry handling

On attempt failure with class `c`: if `c` is in `non_retryable` or not in `retry_on`, or `attempt_count >= max_attempts`, or `predicate` evaluates false, the instance fails. Otherwise a `wait` row of kind `retry_backoff` is created with `wake_at = now + min(initial_interval × backoff^(n−1), max_interval)`; on wake the instance becomes `ready` again and the next attempt is created with the chosen strategy: `fresh` (new workspace from the declared revision), `fresh_with_feedback` (new workspace from the previous attempt's `VcsRef` when present, `Feedback` DTO passed to `build_invocation`), `resume` (previous attempt's `scratch.session_handle`; falls back to `fresh_with_feedback` with `strategy_fallback` set when the runtime lacks `supports_resume`, when the handle is gone, or when the installed harness version differs from the one recorded on the previous attempt, review item 20). Sub-graph iterations follow the retry policy of the sub-graph with clear-downstream and body re-materialisation instead of a new attempt.

Manual retry (FR-6.8) is the clear-downstream transaction started by the API (data-model.md section 6); readiness does the rest. `use_latest_graph_version` re-pins the run to a newer version only when explicitly requested (FR-1.7). Each attempt snapshots `price_table_id`, the runtime `image_digest` and the installed `harness_version`, so a price table or image changed mid-run is attributable per attempt.

## 8. Restart and multi-scheduler safety

- Per-row passes (triggers, timeouts) claim rows with `UPDATE … WHERE status = … AND (lease_until IS NULL OR lease_until < now)`; per-run passes take the run lock first. MVP runs one scheduler but the statements are already claim-based (NFR-1.2 HA path).
- Actions are idempotent on `(run id, node path, attempt id)` (NFR-2.2): re-running a pass after a crash re-derives the same decisions from rows; side effects are guarded by `submission_key`, `approval_request.id`, `inbound_event` idempotency per `(namespace, name)` and the `idempotency_record` table.
- On restart the scheduler performs a **recovery pass** first: expired leases on `claimed`/`submitted`/`running` attempts go through the reclaim check, lookup and adoption. Nothing else needs rebuilding because readiness is a query.

## 9. Scheduling latency (NFR-3.1)

Passes run every 1 s when idle and immediately after any transition (a lightweight in-process notification, or Postgres `LISTEN/NOTIFY`, wakes the loop; SQLite polls). Admission to `runtime.submit` is therefore bounded by one pass plus the worker claim interval, under 5 s at MVP scale. Runtime start-up latency (container start, checkout) is measured separately between `submitted` and the first event and exposed as a metric and on the attempt (`startup_ms`).

## 10. Standalone (SQLite) versus Postgres

| concern | Postgres | SQLite (standalone) |
|---|---|---|
| row claims | `SELECT … FOR UPDATE SKIP LOCKED` | single-writer connection with `BEGIN IMMEDIATE`; one process owns all loops, so claims are plain updates |
| per-run lock | `pg_advisory_xact_lock(hash(run.id))` | implicit: one writer |
| wake-up | `LISTEN/NOTIFY` plus 1 s poll | in-process event plus 1 s poll |
| JSON columns | `jsonb`, indexed on `consumed_versions` keys | `json` text, no partial indexes; sufficient at standalone scale |
| concurrency | n api replicas, n workers | all coroutines in one process; local runtime only by default (Docker allowed) |
| migrations | `kora db upgrade`, online (NFR-7.2) | same migration files |

The schema and all queries are shared; the repository layer has exactly two dialect-specific statements (claim and notify). Anything that works only on SQLite or only on Postgres is a bug by NFR-0.1.

## 11. Metrics and health (NFR-7.3)

`/metrics` exposes: queue depth per runtime (`created` + `queued`), age of the oldest `created` attempt, running attempts, waiting instances by kind, scheduling latency histogram (ready → submitted), startup latency, stalled and lost counts, worker liveness (registered workers by runtime and seconds since last seen), webhook authentication failures, retention last success timestamp and backlog, pending approval age, daily spend and budget-blocked flag per namespace, exporter drops. `/readyz` fails when the DB is unreachable or the recovery pass has not completed.

## 12. Execution backend boundary (NFR-5.5)

The passes in section 2 are the only code that moves `run`, `node_instance`, `attempt` and `wait` rows between states, and the API reaches them through three entry points: start a run, deliver a signal (approval response, inbound event, cancel, manual retry) and tick. That boundary is documented in prose rather than as a Python interface with one implementation (DD-7, review item 9): when the Temporal-backed backend is prototyped after MVP, the generic interpreter workflow replaces the passes and projects transitions into the same rows (data-model.md section 9); readers always read kora's tables.

## 13. Retention task (DD-4)

Retention runs as a separate low-priority task in the scheduler process, paced in small batches with a sleep between them, never inside a scheduling pass. Each batch: select up to N versions past their retention, mark `expiring`, delete each blob only when no other `present` or `expiring` version references the same `storage_ref`, mark `expired`, write `retention.expired` events. The task records its last successful completion for the metric and is safe to re-run at any point (data-model.md section 10).

## 14. Migrations (NFR-7.2)

Alembic migrations are **additive only within a minor version**: columns are added nullable or with defaults, nothing is dropped or renamed until the next minor, and every release runs against the previous schema (N−1 compatibility) so components can be upgraded one at a time. Index creation on PostgreSQL uses `CREATE INDEX CONCURRENTLY` outside a transaction; every DDL statement sets `lock_timeout` so a migration fails fast rather than blocking the scheduler. Upgrade order: database → scheduler → API → workers; a worker on the previous version keeps working because the API accepts the previous request shapes.

## 15. Operability runbook

- **Workers**: `GET /workers` lists the registry (runtime, version, capacity, last seen); a worker that stops heartbeating is visible before any attempt stalls.
- **Storage consistency**: `kora db verify-storage` walks `artifact_version` and `session_segment` rows against the blob store and reports missing or orphaned blobs.
- **Backup and restore**: back up the metadata database first, then the blob store; on restore apply in the same order. Rows whose blobs are missing are marked `expired` by `verify-storage --repair`. Approval channels search for an existing comment carrying the request id before posting, so a restore does not duplicate change-request comments; triggers replay from `trigger_binding.last_fired_key`, so a restored cron does not double-fire.
- **VCS polling**: the approval channel and the built-in sensors poll with exponential back-off and respect the code host's rate-limit headers; results and worker logs are redacted of push URLs and tokens (credential helpers, never URL-embedded secrets).
