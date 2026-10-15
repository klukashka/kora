# kora MVP design — scheduler

Status: design draft for owner review (2026-10-15). Scope: FR-6.1–6.10, FR-1.6, NFR-1, NFR-2, NFR-3, NFR-7. The scheduler is a stateless loop over the tables in [data-model.md](data-model.md); it submits work through the runtime protocol in [protocols.md](protocols.md). Owner decisions on formerly open choices are referenced as **OQ-n** and recorded in [design-decisions.md](design-decisions.md).

## 1. Components and processes

| process | role | MVP deployment |
|---|---|---|
| `kora api` | HTTP API, webhook endpoints, approvals page, MCP-facing routes | 1..n replicas, stateless |
| `kora scheduler` | the loop below: triggers, readiness, submission, waits, invalidation, timeouts | 1 replica in MVP; designed for n with row claims |
| `kora worker` | one per runtime: holds submitted attempts' event streams, heartbeats, enforcement, artifact collection | 1..n per runtime name; a worker is identified by `(runtime, worker_id)`; a worker may run inside an adopter's network and connect outbound to the DB or API, nothing inbound (DD-13) |
| `kora standalone` | api + scheduler + worker(local) in one process on SQLite | development |

The split between scheduler and worker is a decision (DD-13) and follows the Airflow executor model: the scheduler decides and persists; workers perform the runtime calls and stream results back into the DB. In standalone mode they are coroutines in one process.

## 2. The scheduling loop

The scheduler runs the following passes continuously; each pass is a short transaction that claims rows, decides, and writes rows. No pass holds state in memory between iterations (NFR-1.2).

1. **Triggers.** Fire due `trigger_binding` rows (cron `next_fire_at <= now`, sensor polls due through the sensor protocol); create `run` rows with derived idempotency keys; apply the graph's `overlap` (`allow`, the default for every trigger kind: always start; `skip`: no run if one is `running` for the graph; `queue`: create `pending` and start later; DD-12). Webhooks are authenticated by the API (HMAC or bearer per trigger, DD-17) and turned into `inbound_event` rows; the trigger pass converts those addressed to a trigger binding into runs and those addressed to waits into wake-ups.
2. **Materialise.** A `pending` run gets its top-level node instances created from the pinned graph version's IR; subgraph bodies and fan-out instances are materialised lazily when the containing node becomes `ready` (NFR-3.3).
3. **Readiness** (incremental). Only node instances whose upstream changed since the last pass are re-evaluated: the previous pass records `(run_id, node_path)` pairs in a `dirty` set table (`node_dirty`), filled by every transition that touches an instance's dependants. For each dirty instance: evaluate the trigger rule over upstream statuses, resolve inputs to `current` artifact versions, write `consumed_versions`, move to `ready` or `skipped`/`upstream_failed`.
4. **Branch and fan-in.** `ready` branch instances evaluate their expression over consumed artifacts and mark non-chosen targets `skipped`; `ready` fan-in instances collect `current` versions from the body instances and write the list artifact.
5. **Admission.** `ready` instances that need a runtime (`agent`, `command`) become `queued` with an `attempt` row (`created`, `submission_key`), subject to concurrency limits (section 6). Waiting kinds (`human_input`, `wait_event`, `timer`) become `waiting` with a `wait` row and, for approvals, an `approval_request` row (section 5).
6. **Wake-ups.** `wait` rows with `wake_at <= now` or a matching `inbound_event` are resolved; the owning instance returns to `running` (sensor: evaluate `until`, else reschedule), `ready` (retry back-off) or completes (timer).
7. **Timeouts and liveness** (section 4).
8. **Completion.** Attempts in `finishing` whose artifacts are stored move their instance to `succeeded` or `failed`; retry policy is applied (new `queued` attempt with back-off wait, or terminal failure); invalidation (data-model.md section 6) runs when a new version supersedes an old one; the run completes when no instance is non-terminal.
9. **Retention** (nightly, DD-4). For finished runs whose resolved retention has elapsed, delete transcript blobs and artifact payloads through artifact storage, set `payload_state = expired`, write an `event` per deletion; metadata is never deleted.

When an approval request is created (pass 5), the scheduler computes `attempt_cost`, the aggregated usage and USD estimate of every attempt in the lineage of the bound artifact, and stores it on the request so channels and the page can show it (DD-8).

Workers run a separate loop: claim `created` attempts of their runtime (section 3), submit, stream, enforce, finalise.

## 3. At-most-once submission and adoption

```
scheduler:  INSERT attempt(status=created, submission_key=sub:<att>)          -- committed
worker:     UPDATE attempt SET lease_owner=me, lease_until=now+30s, status=submitting WHERE status=created AND runtime=mine  -- claim
worker:     handle = runtime.submit(workload)                                  -- idempotent on submission_key
worker:     UPDATE attempt SET handle=…, status=submitted                       -- committed
worker:     stream events, heartbeat every 10s: UPDATE attempt SET last_heartbeat_at=now, lease_until=now+30s, heartbeat_counters=…
```

- A crash between claim and handle persistence leaves `status=submitting` with an expired lease. The recovery pass calls `runtime.lookup(submission_key)`: a handle means the submission happened and is persisted now; `None` means it did not and the attempt returns to `created`. Either way no second session is started (FR-6.1a, NFR-2.1).
- A crash after `submitted` leaves an expired lease on a live handle. Any worker of that runtime may claim the row and call `runtime.adopt(handle)`, which resumes the event stream from the stored offset (`heartbeat_counters.stream_offset`) and continues enforcement with the persisted counters. Adoption is by `submission_key` lookup when the handle is missing and by handle otherwise.
- Adoption of a runtime that does not know the handle any more yields `lost`, classified `infrastructure`; the runtime-level `infrastructure_retry` policy decides whether to resubmit, and it does not consume the node's agent attempts (NFR-2.4).

## 4. Heartbeats, stall detection and timeouts

Two liveness signals are kept apart (NFR-2.3):

- **Runtime liveness**: the worker's lease (`lease_until`) and `runtime.heartbeat(handle)`. A lease that expires means the worker died; the row is re-claimable. A runtime that reports the session gone means `lost`.
- **Agent-loop liveness**: `last_event_at` in `heartbeat_counters`, advanced by every normalised event. A healthy container whose stream has been silent for `stall_after` is classified `stalled`: the worker cancels the session with grace, the attempt fails with class `stalled`, and the node's retry policy decides. `stall_after` comes from the node's `limits.stall_after`, else the graph's `runtime.stall_after`, else the runtime's `default_stall_after` (15 minutes in the reference runtimes; DD-15).

Timeouts (FR-6.5): the scheduler's timeout pass compares `attempt.started_at + limits.timeout` and `run.started_at + runtime.limits.timeout` with `now`; breaches cancel with grace and fail with class `timeout`. Wait expiry is `wait.wake_at` for timers and `approval_request.expires_at`/`wait.subscription.timeout_at` for approvals and sensors; expiry applies `on_expiry` (NFR-2.5). Subgraph timeouts and budgets are checked against the subgraph instance across attempts (data-model.md section 5).

Grace handling (FR-6.7): `cancel(handle, grace)` sends the harness a termination signal, waits `grace`, then kills; artifacts the session already wrote are collected as `partial`, usage records are marked `partial` (FR-9.1), and the workspace is released after collection.

## 5. Waits hold no worker (NFR-1.3)

A waiting instance is one `wait` row and, for approvals, one `approval_request` row. Nothing else exists: no coroutine, no container, no lease. Three resolution paths:

- **Time**: `wake_at` scanned by the wake-up pass; timers and retry back-off.
- **Event**: the API writes an `inbound_event`; the wake-up pass matches it against open `wait.subscription` rows (`name` equality, then the `match` expression over payload and inputs), marks the wait `satisfied`, records `consumed_by`.
- **Poll**: sensors are polled by the scheduler at `interval` through the sensor protocol (`observe` returns an observation the node's `until` CEL expression is evaluated on); the `vcs` approval channel is polled through the approval protocol. A poll is a short call, not a held slot. Sensors that declare `supports_push` and chat channels with push notifications (post-MVP) deliver through the same `inbound_event` path.

An approval response from any channel is applied through one function: load the request, compare `(commit_sha, digests)` with the bound values and the generation with the instance's generation, then transition. The channel is only a transport (FR-7.2a).

## 6. Concurrency limits (FR-6.10)

Limits are evaluated in the admission pass, in this order, each as a count over rows: global `max_running_attempts` (deployment config), per namespace (`namespace.policy.concurrency`), per graph (`runtime.concurrency.max_running_nodes`, `max_running_runs`), per runtime (`RuntimeCapabilities.max_concurrent` and NFR-7.4 configured limit), per fan-out (`max_parallel`, counted over body instances of the same fan-out instance). A `ready` instance that fails any check stays `ready` with `wake_at = now + admission_interval`; the oldest ready instance wins (FIFO by `node_instance.id`). Limits live in rows and config reloaded every pass, so they change without restart (NFR-7.4).

## 7. Retry handling

On attempt failure with class `c`: if `c` is in `non_retryable` or not in `retry_on`, or `attempt_count >= max_attempts`, or `predicate` evaluates false, the instance fails. Otherwise a `wait` row of kind `retry_backoff` is created with `wake_at = now + min(initial_interval × backoff^(n−1), max_interval)`; on wake the instance becomes `ready` again and the next attempt is created with the chosen strategy: `fresh` (new workspace from the declared revision), `fresh_with_feedback` (new workspace from the previous attempt's `VcsRef` when present, `Feedback` DTO passed to `build_invocation`), `resume` (previous attempt's `scratch.session_handle`; falls back to `fresh_with_feedback` with `strategy_fallback` set when the runtime lacks `supports_resume` or the handle is gone). Subgraph attempts follow the same path with `unit = subgraph` and body re-materialisation.

Manual retry (FR-6.8) is the same path started by the API: it resets the instance to `pending`, bumps generation, and lets invalidation and readiness do the rest; `use_latest_graph_version` re-pins the run to a newer version only when explicitly requested (FR-1.7).

## 8. Restart and multi-scheduler safety

- Every pass claims rows with `UPDATE … WHERE status = … AND (lease_until IS NULL OR lease_until < now)` and a short lease, so two schedulers never act on the same row; MVP runs one scheduler but the statements are already claim-based (NFR-1.2 HA path).
- Actions are idempotent on `(run id, node path, attempt id)` (NFR-2.2): re-running a pass after a crash re-derives the same decisions from rows; side effects are guarded by `submission_key`, `approval_request.id` and `inbound_event.idempotency_key`.
- On restart the scheduler performs a **recovery pass** first: expired leases on `submitting`/`submitted`/`running` attempts go through lookup and adoption; dirty sets are rebuilt from instances whose `updated_at` is newer than their dependants' last evaluation.

## 9. Scheduling latency (NFR-3.1)

Passes run every 1 s when idle and immediately after any transition (a lightweight in-process notification, or Postgres `LISTEN/NOTIFY`, wakes the loop; SQLite polls). Admission to `runtime.submit` is therefore bounded by one pass plus the worker claim interval, under 5 s at MVP scale. Runtime start-up latency (container start, checkout) is measured separately between `submitted` and the first event and exposed as a metric and on the attempt (`startup_ms`).

## 10. Standalone (SQLite) versus Postgres

| concern | Postgres | SQLite (standalone) |
|---|---|---|
| row claims | `SELECT … FOR UPDATE SKIP LOCKED` | single-writer connection with `BEGIN IMMEDIATE`; one process owns all loops, so claims are plain updates |
| wake-up | `LISTEN/NOTIFY` plus 1 s poll | in-process event plus 1 s poll |
| JSON columns | `jsonb`, indexed on `consumed_versions` keys | `json` text, no partial indexes; sufficient at standalone scale |
| concurrency | n api replicas, n workers | all coroutines in one process; local runtime only by default (Docker allowed) |
| migrations | `kora db upgrade`, online (NFR-7.2) | same migration files |

The schema and all queries are shared; the repository layer has exactly two dialect-specific statements (claim and notify). Anything that works only on SQLite or only on Postgres is a bug by NFR-0.1.

## 11. Metrics and health (NFR-7.3)

`/metrics` exposes: queue depth per runtime (`created` + `queued`), running attempts, waiting instances by kind, scheduling latency histogram (ready → submitted), startup latency, stalled and lost counts, exporter drops. `/readyz` fails when the DB is unreachable or the recovery pass has not completed.

## 12. Execution backend interface (NFR-5.5)

The passes in section 2 are implemented against an internal interface so that a durable-execution engine could replace them:

```python
class ExecutionBackend(Protocol):
    async def start_run(self, run_id: str) -> None
    async def signal(self, run_id: str, signal: RunSignal) -> None   # approval response, inbound event, cancel, retry node
    async def tick(self) -> None                                      # kora backend: one pass; Temporal backend: no-op
```

Everything the API does goes through `start_run` and `signal`; readers always read kora's tables. The kora backend is the loop above; a Temporal backend would run a generic interpreter workflow per run and project transitions into the same rows (data-model.md section 9). The owner decided to prototype that backend after MVP, by demand, and to keep this interface documented until then (DD-7).
