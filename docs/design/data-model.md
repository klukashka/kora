# kora MVP design — control-plane data model

Status: design draft for owner review (2026-10-15). Scope: the metadata-database entities, their state machines and the invariants the scheduler relies on (FR-4, FR-6, FR-7, FR-9, FR-12.4–12.5, NFR-1.2, NFR-2, NFR-4.3, NFR-5.5). Companion documents: [ir.md](ir.md) (what a graph version contains), [scheduler.md](scheduler.md) (who writes these rows and when), [api-and-agent-surface.md](api-and-agent-surface.md) (how they are exposed). Owner decisions on design choices are referenced as **DD-n** ("design decision n") and recorded in [design-decisions.md](design-decisions.md).

## 1. Principles

1. **Run state is interpretable data** (NFR-5.5, decision 12). Every row below is plain JSON-compatible data; no pickled objects, no callables (NFR-4.3, FR-7.5). The scheduler is a pure function from rows to actions, so a Temporal-backed execution backend could later own the `run`/`node_instance`/`attempt`/`wait` rows' transitions while `artifact_version`, `approval_request`, `usage_record` and the registry stay in kora's DB unchanged.
2. **Multi-tenant from the first table.** Every entity carries `namespace_id`; every query is scoped by it; ids are globally unique (ULIDs), so no composite tenant keys are needed in URLs.
3. **Durable before side effects** (NFR-2.1). An attempt row with its `submission_key` is committed before `runtime.submit`; an approval request row is committed before `channel.request`; a trigger row is committed before a run is created.
4. **Append, do not overwrite.** Superseded results stay as rows with a status; the timeline is an append-only `event` table.
5. **Same schema on SQLite and Postgres.** Only the claim/lease statements differ (see scheduler.md); no feature exists only in standalone mode (NFR-0.1).

## 2. Entities

Registry and tenancy:

| table | key fields | notes |
|---|---|---|
| `namespace` | `id`, `name`, `policy` (JSON: concurrency limits, default runtime, default retention) | FR-12.4; retention defaults per DD-4 |
| `graph` | `id`, `namespace_id`, `name` | stable identity across versions |
| `graph_version` | `id`, `graph_id`, `number`, `ir` (JSON), `ir_digest`, `source_digest`, `deployed_by`, `deployed_at`, `status` (`active`, `retired`) | FR-12.5; runs pin `graph_version_id` (FR-1.7) |
| `preset` | `id`, `namespace_id`, `name`, `version`, `body` (JSON) | published by the platform team; snapshot copied into the IR at compile time |
| `artifact_type` | `id`, `namespace_id` (null for built-ins), `name`, `schema` (JSON), `parent` | FR-4.3 registry; assignability for edge checks |
| `api_token` | `id`, `namespace_id`, `digest`, `scopes` (JSON), `owner` (user or service identity), `expires_at` | MVP: one static token; the MCP server and the approvals page act with the user's token (DD-5, DD-19) |
| `trigger_binding` | `id`, `graph_id`, `kind`, `spec` (JSON: cron, webhook `auth`, sensor name and config), `next_fire_at`, `last_fired_key` | cron/webhook/sensor rows the scheduler polls; sensors run through the sensor protocol (DD-2) |

Execution:

| table | key fields | notes |
|---|---|---|
| `run` | `id`, `namespace_id`, `graph_id`, `graph_version_id`, `idempotency_key` (unique per graph), `params` (JSON), `status`, `trigger` (JSON), `limits` (JSON), `retention` (JSON, resolved from graph and namespace), `started_at`, `finished_at`, `generation` | one row per run of a graph version |
| `node_instance` | `id`, `run_id`, `node_path` (e.g. `candidates[2].review`), `kind`, `status`, `trigger_rule`, `consumed_versions` (JSON: input name → `artifact_version_id`), `current_attempt_id`, `attempt_count`, `generation`, `wake_at`, `superseded_by` | materialised per fan-out instance and per subgraph attempt; `node_path` is the join key for logs and UI |
| `attempt` | `id`, `node_instance_id`, `number`, `unit` (`node` or `subgraph`), `submission_key` (unique), `runtime`, `handle` (JSON, nullable until known), `status`, `error_class`, `error` (JSON), `strategy`, `strategy_fallback`, `lease_owner`, `lease_until`, `last_heartbeat_at`, `heartbeat_counters` (JSON: events, tokens, usd), `scratch` (JSON: resumable session handle, FR-4.6), `resolved_config_digest`, `started_at`, `finished_at` | the unit of submission and of cost |
| `wait` | `id`, `node_instance_id`, `attempt_id`, `kind` (`timer`, `approval`, `event`, `poll`, `retry_backoff`), `wake_at`, `subscription` (JSON: webhook name + match expression, or poll spec), `status`, `generation` | NFR-1.3: a waiting node is a row, never a worker; the scheduler scans `wake_at <= now` and matches incoming events against `subscription` |
| `approval_request` | `id`, `node_instance_id`, `attempt_id`, `channel`, `channel_request_id`, `form` (JSON Schema), `context_artifact_version_id`, `bound_commit_sha`, `bound_digests` (JSON: path → digest), `attempt_cost` (JSON: aggregated usage and USD estimate of every attempt in the bound artifact's lineage, with completeness), `status`, `response` (JSON), `approver` (JSON: identity, channel, timestamp), `expires_at`, `generation` | FR-7.2a; `(bound_commit_sha, bound_digests)` are compared on every response; `attempt_cost` is computed by the scheduler when the request is created (DD-8) |
| `artifact_version` | `id`, `run_id`, `node_instance_id`, `attempt_id`, `output_name`, `type_name`, `digest`, `storage_ref`, `size`, `inline` (JSON when small), `status` (`current`, `superseded`, `invalid`), `payload_state` (`present`, `expired`), `created_at` | FR-4.1, FR-4.5; one row per attempt output; the "artifact" a user talks about is `(node_path, output_name)` and its versions; `payload_state` turns `expired` when retention deletes the payload (DD-4) |
| `artifact_lineage` | `artifact_version_id`, `input_name`, `consumed_artifact_version_id`, `relation` (`consumed`, `reused_invariant`, `previous_attempt`) | derived at attempt start from `node_instance.consumed_versions`; queryable both ways |
| `usage_record` | `id`, `attempt_id`, `model`, `input_tokens`, `cached_input_tokens`, `cache_creation_tokens`, `output_tokens`, `reasoning_tokens`, `completeness` (`complete`, `partial`, `unavailable`), `basis` (`harness_list`, `harness_managed`, `price_table`, `gateway`), `billing_class` (`api_key`, `subscription`, `gateway`, `managed`), `usd_estimate`, `delta_of_session_totals` (bool), `source` (JSON: raw harness figures) | FR-9.1–9.3; one row per model per attempt; run/node totals are sums, never stored |
| `price_table` | `id`, `namespace_id` (null = default), `model`, `effective_from`, `prices` (JSON) | FR-9.2 |
| `transcript` | `id`, `attempt_id`, `harness`, `harness_version` (installed version, recorded, never compared to a node requirement, DD-14), `storage_ref`, `payload_state`, `format_tag`, `event_summary` (JSON) | NFR-9.3: opaque blob plus normalised summary |
| `event` | `id`, `namespace_id`, `run_id`, `node_instance_id`, `attempt_id`, `kind`, `actor`, `payload` (JSON), `at` | append-only timeline and audit log (FR-11.1, FR-11.4); approval decisions, permission denials, VCS side effects and retention deletions all land here |
| `vcs_side_effect` | `id`, `attempt_id`, `operation` (`checkout`, `publish`, `open_change_request`), `result` (JSON: VcsRef or url), `at` | FR-6.1b record of what kora itself did through the VCS protocol |
| `inbound_event` | `id`, `namespace_id`, `name`, `idempotency_key` (unique), `payload` (JSON), `auth_kind` (`hmac`, `bearer`, `internal`), `received_at`, `consumed_by` (JSON list of wait ids) | webhooks (authenticated per DD-17) and graph-to-graph events, deduped before matching |

## 3. Identifiers and keys

- All primary keys are ULIDs with a type prefix in the API (`run_01J…`, `att_01J…`, `art_01J…`), sortable by creation time.
- `run.idempotency_key`: supplied by the caller (`Idempotency-Key` header, CLI `--idempotency-key`) or derived: cron → `cron:<graph_version>:<fire time>`, webhook → the delivery id, graph trigger → `<parent run id>:<event>`. Unique per `graph_id`; a duplicate returns the existing run (NFR-2.2).
- `attempt.submission_key` = `sub:<attempt id>`, generated and stored before `submit`; runtimes dedupe on it and `lookup(submission_key)` recovers a handle after a crash (FR-6.1a).
- `approval_request.channel_request_id`: the channel's own id (PR review id, page token), stored after `channel.request` returns; the approval request row exists before the call so that a crash in between is recovered by re-requesting with the same `approval_request.id` as the channel idempotency key.
- `generation`: an integer on `run`, `node_instance`, `wait` and `approval_request`. Invalidation (section 6) bumps the downstream instances' generation; any late response, heartbeat or event that carries an older generation is ignored and logged.

## 4. State machines

Terminal states are in bold. Every transition writes an `event` row in the same transaction.

**run**: `pending` → `running` → **`succeeded`** | `failed` | **`cancelled`**. `failed` is not terminal: `retry node` (FR-6.8) moves it back to `running`; `cancel` moves it to `cancelled`, which discards nothing but accepts no more actions (Archon's failed-resumable vs cancelled-discarded split). `pending` exists only between trigger acceptance and the first scheduling pass; `running` includes "all remaining nodes are waiting".

**node_instance**: `pending` → `ready` (trigger rule satisfied, inputs resolved) → `queued` (attempt created, waiting for a concurrency slot) → `running` → **`succeeded`** | `failed` | **`skipped`** | **`upstream_failed`** | `waiting` | **`superseded`**. `waiting` is entered by human_input, wait_event, timer and retry back-off and returns to `running` or `ready` when the wait resolves. `failed` returns to `pending` on manual retry or on upstream invalidation. `superseded` marks an instance replaced by a newer generation (its rows stay for inspection). `skipped` is set by branch non-selection, trigger rules and `on_expiry: skip`.

**attempt**: `created` → `submitted` (handle persisted) → `running` (first heartbeat or status) → `finishing` (session ended, artifacts being validated and stored) → **`succeeded`** | **`failed`** (with `error_class`) | **`cancelled`** | **`lost`**. `stalled` is an `error_class`, not a state: a stalled attempt is failed with class `stalled`, which the policy may retry. `lost` means adoption failed after restart: `lookup(submission_key)` found nothing and the runtime reports no such handle; it is classified `infrastructure` and does not consume agent attempts (NFR-2.4). Subgraph attempts (`unit = subgraph`) have no runtime handle; their state is derived from their body's node instances and they end `failed` with class `attempt_failed` when a branch routes to `$fail_attempt`.

**wait**: `open` → **`satisfied`** | **`expired`** | **`cancelled`**. Expiry applies the node's `on_expiry`. Cancellation happens on run cancel, on invalidation (generation bump) and when the owning attempt is failed by a timeout.

**approval_request**: `pending` → **`approved`** | **`rejected`** | **`expired`** | **`invalidated`** | **`cancelled`**. A response whose `(commit_sha, digests)` differ from the bound values is rejected with error code `approval_stale` and the row stays `pending` (FR-7.2a). A VCS channel observing a new push moves the row to `invalidated`; the node then either re-requests or reruns gates per `on_new_push`. `rejected` fails the node with class `approval_rejected`.

**artifact_version**: `current` → `superseded` (a newer version of the same `(node_path, output_name)` in the same run) or `invalid` (schema validation failed; the producing attempt fails with class `artifact_invalid`). Never deleted by scheduling actions.

## 5. Attempts, lineage and sub-graph retry

- A node attempt stores `consumed_versions` on its `node_instance` at the moment it becomes `ready`; the attempt's outputs get `artifact_lineage` rows pointing to those versions. Cost per accepted change (NFR-8.3) is a lineage walk: from the accepted `VcsRef` version back through `consumed` and `previous_attempt` relations, summing `usage_record` rows of every attempt visited, plus the fan-out siblings of the same `fan_out` node.
- A **subgraph attempt** is an `attempt` row with `unit = subgraph` on the subgraph's own `node_instance`. Its body is materialised as node instances whose `node_path` carries the attempt number: `candidates[2]@3.review`. Attempt N+1 materialises a fresh body; nodes marked `attempt_invariant` are not materialised again, and their consumers get `artifact_lineage.relation = reused_invariant` rows pointing to the attempt-N version. The `PreviousAttempt` artifact is written by the scheduler as an `artifact_version` of the subgraph instance (`output_name = previous_attempt`) so that lineage and the API treat it like any artifact.
- Limits spanning attempts (subgraph `timeout`, `budget_usd`, `max_attempts`) are evaluated against the subgraph `node_instance`: `started_at` of its first attempt and the sum of `usage_record.usd_estimate` over all attempts of all body instances.

## 6. Downstream invalidation on upstream retry (FR-6.8a)

When an attempt of node instance U produces a new `current` artifact version V' for output o:

1. The previous version V is marked `superseded`.
2. For every node instance D in the same run whose `consumed_versions` contains V and whose IR node does not list U in `independent_of`: D's `generation` is incremented, open `wait` and `approval_request` rows of D are `cancelled` (approval requests become `invalidated`), a running attempt of D is cancelled with class `cancelled` and reason `upstream_superseded`, D's artifact versions are marked `superseded`, and D returns to `pending`. The step repeats transitively through D's consumers.
3. Fan-in instances whose fan-out body was touched return to `pending` and recollect.
4. The scheduler re-evaluates trigger rules; D runs again when satisfied, with the new `consumed_versions`.

Because every late message carries the generation of the instance it belongs to, a response to a cancelled approval or a heartbeat from a cancelled attempt is discarded, not applied.

## 7. Usage records and completeness (FR-9.1)

One `usage_record` per `(attempt, model)`. `completeness` is `complete` when the harness's final result event was parsed, `partial` when the figures were summed from streamed per-turn events after a kill, crash or `max_events` termination, `unavailable` when nothing was reported (for Codex a killed turn is unaccounted and recorded as `partial` with the last completed turn's totals). For `strategy = resume` the harness reports whole-session totals; the adapter stores the delta against the totals recorded on the previous attempt and sets `delta_of_session_totals`. `usd_estimate` is always derived at write time from `basis`: harness-reported figures when `basis` is `harness_list` or `harness_managed`, else the active `price_table`. Totals shown by the API are sums over rows and carry the weakest completeness of their parts.

## 8. Entity relationships

```
namespace 1─n graph 1─n graph_version 1─n run 1─n node_instance 1─n attempt 1─n artifact_version
                                                      │                 ├─n usage_record
                                                      ├─n wait          ├─1 transcript
                                                      └─n approval_request
artifact_version n─n artifact_version   (artifact_lineage)
run / node_instance / attempt ─n event  (timeline, audit)
```

## 9. Compatibility with a Temporal-backed backend (NFR-5.5)

The internal `ExecutionBackend` interface in [scheduler.md](scheduler.md) owns transitions of `run`, `node_instance`, `attempt` and `wait`. A Temporal backend would keep those four as projections written by workflow code (so the API still reads kora's DB), while `artifact_version`, `artifact_lineage`, `approval_request`, `usage_record`, `transcript`, `event` and the registry are written by activities through the same repository layer. Nothing in the API or the protocols depends on which backend performs the transitions. Two rules keep the door open: no table stores backend-specific handles except `attempt.handle` and `attempt.lease_*`, and the scheduler never relies on in-memory state between passes (NFR-1.2).

## 10. Retention (DD-4)

Retention is a graph-level setting (`runtime.retention` in the IR) with a namespace default (`namespace.policy.retention`); the resolved values are copied onto each `run` at creation. Defaults:

| data | default | what the nightly job does |
|---|---|---|
| run, node instance, attempt, event and usage records | forever | nothing |
| session transcripts | 90 days after the run finished | deletes the blob through artifact storage; the `transcript` row stays with `payload_state = expired` and its `event_summary` |
| artifact payloads (files, structured outputs, command output captures) | 180 days after the run finished | deletes the blob; the `artifact_version` row stays with `payload_state = expired`, digest and lineage intact |
| reference-only artifacts (`VcsRef`, `AgentSession` metadata, other types whose payload is a reference) | forever | nothing |

The job runs nightly in the scheduler (scheduler.md section 2), deletes payloads only, never metadata, and writes an `event` per deletion so the timeline shows what is gone. Approval requests keep `attempt_cost` and bound digests regardless.
