# Own scheduler vs existing engine: kora as a library on top of Temporal

Status: desk analysis, 2026-10-15. Tests the hypothesis "own scheduler is necessary" in `positioning.md` (own scheduler vs existing engine + adapter); the combined verdict is in `verdict.md` against Temporal (Python SDK, self-hosted or Temporal Cloud). No code written. Sources: `docs/requirements/functional.md`, `docs/requirements/non-functional.md`, `knowledge/research/agentic-pipeline-orchestrators/*.md`, official Temporal docs cited inline.

## Adapter model assumed

- **Run** = one execution of a single generic `KoraRunWorkflow(ir_ref, params)` written once by kora: it interprets the IR as data. User graphs never become workflow code.
- **Agent / command node attempt** = one Activity (`claude -p --bare --output-format stream-json`, `codex exec --json`, shell) heartbeating every 10–30 s with counters (events, tokens, USD, session id). Results are artifact *references*.
- **Sub-graph** = child workflow; **fan-out** = parallel children with a sliding-window cap; **approval** = Signal/Update + validator + `wait_condition(timeout)`; **sensor** = timer + short poll Activity; **timer** = `asyncio.sleep`; **branch** = workflow code.
- **Attempts with feedback** (`fresh_with_feedback`, `resume`, sub-graph retry with `previous_attempt`) = a kora loop inside the workflow. Temporal's RetryPolicy is reserved for infrastructure errors, which implements NFR-2.4 directly.
- **Transcripts, event streams, artifacts, cost ledger, registry, approvals page** stay in kora's DB/blob store. Temporal owns run state, timers, waits, infra retries, child lifecycle, audit history.

## Classification

S/M/L = adapter size. "Engine-neutral" = same work as with an own scheduler.

| Requirement | Class | Notes / source |
|---|---|---|
| FR-1.1 Python DSL; FR-1.3 IR sections; FR-1.4 compile/validate | adapter (M) | Engine-neutral. |
| FR-1.2 IR consumed only by scheduler | adapter (M) | Interpreter workflow reads IR via a journaled Activity; evaluation runs in the deterministic workflow sandbox ([sandbox](https://docs.temporal.io/develop/python/python-sdk-sandbox)). |
| FR-1.5 other SDKs / UI editor | adapter (S) | Works because the graph is data, not code. |
| FR-1.6 trigger rules | adapter (M) | No DAG in Temporal; interpreter keeps a node-state table. |
| FR-1.7 IR hash per run | native | Workflow input is immutable in history. |
| FR-2.1 agent node | adapter (M) | Long-running Activity pattern ([pattern](https://docs.temporal.io/design-patterns/long-running-activity)). |
| FR-2.2 approval gate, no slot held | native + adapter (S) | Update validator rejects bad/stale responses without touching history ([message passing](https://docs.temporal.io/develop/python/message-passing)); form schema, expiry behaviour are kora. |
| FR-2.3 sensor | native + adapter (S) | Polling pattern: timer + short Activity. |
| FR-2.4 timer | native | Persisted timers, up to 100 years ([limits](https://docs.temporal.io/cloud/limits)). |
| FR-2.5 branch | adapter (S) | Deterministic code on an artifact summary (≤2 MB payload). |
| FR-2.6 fan-out/fan-in, cap, skip_failures | native + adapter (S) | Fan-out / Sliding-Window patterns ([catalogue](https://docs.temporal.io/design-patterns)); 2,000 pending children hard cap, 500 recommended ([limits](https://docs.temporal.io/workflow-execution/limits)). |
| FR-2.7 sub-graph as unit | adapter (M) | Child workflow with own execution timeout and explicit `parent_close_policy` ([child workflows](https://docs.temporal.io/develop/python/child-workflows)); unit retry loop in parent. |
| FR-2.7a iteration semantics | adapter (M) | Kora loop passes `previous_attempt` refs; attempt-invariant memoisation via kora DB; cross-attempt budget in workflow state. Native retry cannot pass prior results except heartbeat details. |
| FR-2.8 command node | adapter (S) | Activity. |
| FR-3.1–3.4 config, inheritance, mapping, `trust_repo_config` | adapter (M) | Engine-neutral; resolved config by reference. |
| FR-4.1 typed artifacts, lineage; FR-4.2 built-ins incl. ReviewBundle; FR-4.3 custom types | adapter (L) | No artifact model in Temporal; kora DB as planned. |
| FR-4.4 storage protocol, by-digest refs | adapter (M) | Forced anyway by 2 MB payload / 4 MB gRPC limits; External Storage + codec server is the documented path ([encryption](https://docs.temporal.io/production-deployment/data-encryption)). |
| FR-4.5 per-attempt retention | adapter (S) | Kora DB; history keeps each attempt's result too. |
| FR-4.6 scratch vs output | native + adapter (S) | Heartbeat details survive native retries; loop attempts keep scratch in workflow state. |
| FR-5.1–5.3 workspace, VCS protocol | adapter (M) | Inside Activities; engine-neutral. |
| FR-6.1 control plane, restart resumes | native + adapter (M) | Temporal owns run state; kora keeps API, registry, artifact/cost DB. Two stores to reconcile. |
| FR-6.1a at-most-once submission | adapter (S) | Activities are at-least-once; Activity persists `submission_key` before `submit`, re-looks up on retry. Run starts dedupe by workflow id ([ids](https://docs.temporal.io/workflow-execution/workflowid-runid)). |
| FR-6.1b side-effect boundary | adapter (S) | Policy; history audits every input/result. |
| FR-6.2 triggers | native + adapter (S) | Schedules with overlap/backfill/pause ([schedules](https://docs.temporal.io/schedule)); webhook = kora endpoint → idempotent `start_workflow`; graph→graph = child or Signal-with-Start. |
| FR-6.3 retry policy fields | native + adapter (S) | RetryPolicy has the same fields incl. `non_retryable_error_types` ([retry](https://docs.temporal.io/encyclopedia/retry-policies)); artifact predicates need the kora loop. |
| FR-6.4 fresh / resume / fresh_with_feedback | adapter (M) | Kora loop; resume handle from scratch state; fallback recorded. |
| FR-6.5 timeouts | native | Activity, heartbeat, workflow run/execution, `wait_condition`. |
| FR-6.6 `max_events` | adapter (S) | Activity counts JSONL events, kills subprocess, raises `ApplicationError("events_exhausted")`. |
| FR-6.7 cancellation with grace | native + adapter (S) | Delivered only on heartbeat ([cancellation](https://docs.temporal.io/develop/python/cancellation)); grace is Activity code. |
| FR-6.8 manual node re-run inside a run | adapter (M) | Run must stay open in "failed-awaiting" and accept a `retry_node` Signal; Reset is whole-execution, not per node. Closed run → new workflow seeded from kora DB. |
| FR-6.8a downstream invalidation | adapter (M) | Node-state table binds results to consumed versions; cancel dependents' handles, bump a generation so waits and validators reject stale input. No engine help beyond cancellation. |
| FR-6.9 quotas | adapter (S) | Kora; Schedule pause native. |
| FR-6.10 concurrency limits | native (per-runtime) + adapter (M) | Worker slots and queue throttling only; no namespace/type semaphore ([task queue](https://docs.temporal.io/task-queue), [worker perf](https://docs.temporal.io/develop/worker-performance)); global/per-graph limits need a kora semaphore. |
| FR-7.1 channel protocol; FR-7.2 CLI/API + VCS channels; FR-7.4 chat | adapter (M) | `request` = notification Activity; `notify` = Signal/Update; VCS = poll Activity → Signal. |
| FR-7.2a SHA-bound approval | adapter (S) | Validator compares `(sha, digests)` with the recorded request; new push invalidates. |
| FR-7.3 minimal web page | adapter (S) | Temporal UI has no forms. |
| FR-7.5 gate state as data | native | JSON payloads only. |
| FR-8.1 runtime protocol | adapter (M) | Activity = `submit` + poll + heartbeat; `adopt` = lookup by key at attempt start; async completion via task token for callback runtimes ([async completion](https://docs.temporal.io/develop/python/asynchronous-activity-completion)). |
| FR-8.2 local/Docker; FR-8.3 sandbox/K8s; FR-8.5 secrets; FR-8.6 network flags | adapter (M) | Workers per runtime; engine-neutral. |
| FR-8.4 per-node runtime override | native | Task queue per runtime; `execute_activity(task_queue=...)`. |
| FR-8.7 enterprise runtime (in-house CI and execution platform) | adapter (M) | Worker inside the enterprise network polls out to a self-hosted Temporal; submits tasks to the execution platform, polls or completes async. Reachable; the gate is permission to run Temporal. |
| FR-9.1 token ledger, completeness | adapter (M) | Activity reads harness usage, streams totals in heartbeats, writes final record; `partial` from last heartbeat on kill. |
| FR-9.2 price table; FR-9.3 billing class; FR-9.6 reconciliation | adapter (M) | Engine-neutral. |
| FR-9.4 soft USD budget | adapter (S) | Activity-side termination; `--max-budget-usd` passthrough. |
| FR-9.5 cost views | adapter (M) | Workflow state + kora DB; per-run USD as a Double Search Attribute (3 allowed on SQL visibility) ([search attributes](https://docs.temporal.io/search-attribute)). |
| FR-10.1 secrets protocol; FR-10.2 redaction | adapter (S) + native | Only references cross Temporal (history persists all inputs); payload codec encrypts at rest; codec server for UI. |
| FR-11.1 status/timeline | native + adapter (M) | Temporal UI/CLI show workflows/activities, not nodes; kora API maps. |
| FR-11.2 transcripts | adapter (S) | Outside Temporal by design. |
| FR-11.3 exporters, OTel GenAI | adapter (M) | SDK OTel interceptor gives workflow/activity spans; GenAI spans are kora's. |
| FR-11.4 audit log | native + adapter (S) | Event History audits inputs/results/Signals; VcsRef linkage in kora DB. |
| FR-11.5 web UI | adapter (L) | Temporal UI is not a graph view. |
| FR-12.1 components | native + adapter | Temporal services, DB, UI (+ES, codec server) **plus** kora API, DB, workers, approvals page. |
| FR-12.2 CLI | adapter (M) | Run commands wrap the client; compile/validate/deploy/retry are kora. |
| FR-12.3 docker-compose | adapter (M) | `temporalio/auto-setup` + Postgres + UI + kora ([setup](https://docs.temporal.io/self-hosted-guide/setup)). |
| FR-12.4 namespaces | native | Admin-created Temporal namespaces. |
| FR-12.5 versioned registration | adapter (S) | Registry in kora DB; runs pin IR hash. |
| NFR-1.1 scale, days-long runs; NFR-1.2 stateless HA; NFR-1.3 waits hold nothing; NFR-1.4 fan-out 100 | native | History stays small (≈4 events per Activity; heartbeats add none) when transcripts are external; 51,200-event / 50 MB cap not approached. |
| NFR-2.1 durable before side effect | native + adapter (S) | Schedule journaled before execution; key dedupe in Activity. |
| NFR-2.2 idempotent actions, trigger keys | native | Workflow-id conflict policy `Use Existing`. |
| NFR-2.3 heartbeat / stalled; NFR-2.5 bounded waits | native | |
| NFR-2.4 infra vs agent errors | native + adapter (S) | Infra → RetryPolicy; agent → kora loop. |
| NFR-6.1–6.4 IR canonical, SDK is a client, migrator, file classes | adapter (S) | Preserved by the interpreter design. Cost: interpreter changes need `patched()` or pinned Worker Versioning ([versioning](https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning/)). |
| NFR-7.1 single-command standalone, SQLite | **blocked** (prod) / adapter (dev) | `start-dev` is "not intended for production"; SQLite persistence is "development and testing only" ([cli](https://docs.temporal.io/cli/server), [persistence](https://docs.temporal.io/self-hosted-guide/persistence)). Production minimum is Temporal + Postgres. |
| NFR-7.2 migrations; NFR-7.3 health/Prometheus; NFR-7.4 capacity per runtime | native + adapter (S) | Two schema tools; server/SDK metrics are Prometheus; queue backlog per runtime. |

## Hard blockers

1. **No production single-process/SQLite mode.** The smallest production deployment is "Temporal server + its database + kora". `kora standalone` survives only as a dev wrapper around `start-dev --db-filename`.
2. **Two sources of truth.** Run state is Temporal history; artifacts, lineage, cost, approvals, registry are kora's DB. Node-level views, `kora retry <run> <node>` and recovery after partial failures must bridge both; Reset is unusable per node.
3. **No global or per-graph concurrency primitive.** Only per-runtime (worker slots, queue throttling); kora builds its own semaphore.
4. **Determinism tax on kora's interpreter.** Every change to node-state logic needs patch markers or pinned Worker Versioning plus replay tests; days-long runs amplify it. Users are unaffected because graphs are data.

None is "cannot be done"; the first changes product shape, the rest are costs.

**Not blocked after verification:** event-history limits (sessions are single Activities, transcripts external); payload limits (2 MB matches by-digest references); enterprise runtimes (workers poll out, nothing inbound); IR/UI round-trip (one fixed interpreter workflow).

## What Temporal gives for free

Durable run state and crash recovery; timers and waits holding no worker; Signal/Update with validators; per-attempt RetryPolicy with exactly FR-6.3's fields; heartbeats, stall detection, cancellation delivery; child-workflow lifecycle and fan-out patterns; workflow-id dedupe for triggers; Schedules with overlap/backfill/pause; namespaces; Event History as audit log; UI and CLI for runs; Prometheus metrics; OTel interceptors; payload encryption; Worker Versioning so long runs finish on the build that started them; Nexus for cross-namespace graph calls ([nexus](https://docs.temporal.io/nexus)); scale to NFR-1.1 without redesign.

## Operations and cost

**Self-hosted:** Temporal frontend/history/matching/worker (one `auto-setup` container at small scale), PostgreSQL (also visibility; Elasticsearch optional), Temporal UI, codec server if encrypting, **plus** kora API, DB, workers per runtime, approvals page. Versus kora's own compose (Postgres, scheduler, API, runtime): three to four more containers and a second migration tool. Server is MIT-licensed.

**Temporal Cloud:** Actions include every Activity start/retry, server-received heartbeat, timer, Signal, Update, Query, and two per child start ([actions](https://docs.temporal.io/cloud/actions)); $50/M tapering to $25/M, active storage $0.042/GBh, retained $0.00105/GBh; Essentials from $100/mo, Business from $500/mo ([pricing](https://docs.temporal.io/cloud/pricing)). A run shaped like the SQL optimisation graph (≈60 Activities, 9 children, 2-hour sessions heartbeating every ~4 min) is ≈1,500–3,000 Actions; 10,000 runs/month ≈ 15–30M Actions ≈ $700–1,300/month plus plan fee. Storage negligible.

## Answers to the specific questions

**IR vs workflows-as-code.** One generic interpreter workflow takes the IR as data; DSL → IR → other SDKs/UI are untouched. Temporal UI shows a flat activity list, so kora still owns the node-level run view.

**Enterprise runtime.** Adopter implements the runtime protocol as Activity code (submit a task to the execution platform, poll or async-complete, cancel) in a kora worker inside the enterprise network against a self-hosted Temporal; the non-git VCS is a VCS plugin in the same worker.

**Cost and `max_events`.** Both computed in the Activity from the JSONL stream; running totals ride on heartbeats (visible in `describe`), the final `AgentSession` goes to kora's DB, run aggregates live in workflow state and Search Attributes. Breach kills the subprocess and raises a typed non-retryable `ApplicationError`.

**Invalidation and SHA-bound approvals.** The interpreter keeps `{node: state, consumed_versions, generation}`. A new upstream version cancels dependents and bumps the generation; approval validators reject responses whose generation or `(sha, digests)` differ from the open request, so stale approvals never enter history.

## Verdict

Temporal can carry kora's execution semantics: nothing in FR 1–12 is impossible and roughly a third of the reliability and scheduling requirements become native. It does not remove kora's own metadata model (artifacts, lineage, cost, approvals, registry) or node-level run control, and it adds a mandatory Temporal deployment, a second state store, a determinism/versioning discipline on the interpreter, and a Cloud bill in the low thousands per month at target scale. The adapter list is long but engine-neutral; the blocker list is short and operational. This analysis therefore does not confirm that an own scheduler is *necessary*; the trade is "write a scheduler and state model" versus "operate Temporal and keep two stores in sync". Next step as written in the hypothesis: express the SQL optimisation graph with a thin interpreter prototype and measure the reconciliation and standalone-mode costs on a real run before freezing MVP scope.
