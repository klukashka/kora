# Own scheduler vs existing engine: kora as an application on the DBOS durable-execution library

Status: desk analysis, checked 2026-10-16. Tests the hypothesis "own scheduler is necessary" from `positioning.md` for the DBOS option only; added after the [architecture critique of 2026-10-16](../../reviews/architecture-critique-2026-10-16.md#how-mature-orchestrators-are-built-and-the-applicable-lessons) found DBOS missing from the comparison (issue #22). The combined verdict is in [verdict.md](verdict.md). No code written. Sources: `docs/requirements/functional.md`, `docs/requirements/non-functional.md`, [durable_execution_observability.md](../../research/agentic-pipeline-orchestrators/durable_execution_observability.md), the official DBOS documentation cited inline, and the Python package metadata on GitHub (`dbos` 3.3.0 released 2026-10-15, MIT, Python 3.10 or later, SQLAlchemy 2 and psycopg 3). Several Conductor and admin API pages of the documentation returned 404 on the date checked; only pages that were read are cited, so the admin recovery endpoint is not described here.

## Adapter model assumed

- **Run** = one execution of a single generic `run_graph(ir_ref, params)` DBOS workflow written once by kora: a deterministic function that interprets the IR as data, with the kora run id as the workflow id. User graphs never become workflow code.
- **Agent / command node attempt** = one DBOS step that submits the attempt to a kora worker over the HTTP boundary (ADR-0003) and polls until the attempt ends. The step persists a submission key before `submit` and looks the submission up when it is executed again. Results are artifact *references*.
- **Sub-graph** = child workflow; **fan-out** = child workflows on a queue with a concurrency limit; **approval** = `recv` on a topic (or `get_event`) with a timeout, validated in workflow code; **sensor** = durable sleep plus a short poll step; **timer** = `DBOS.sleep`; **branch** = workflow code.
- **Attempts with feedback** (`fresh_with_feedback`, `resume`, sub-graph retry with `previous_attempt`) = a kora loop inside the workflow. Step retries are reserved for infrastructure errors, which implements NFR-2.4 directly.
- **Transcripts, event streams, artifacts, cost ledger, registry, approvals page** stay in kora's tables, written through the same repository layer. The DBOS system database (a second database on the same PostgreSQL server, or a SQLite file beside kora's in standalone) holds workflow checkpoints, step outputs, queue and schedule state.
- **Executors stay inside the control plane.** A process that runs DBOS workflows needs the system database, so the DBOS executor is kora's own scheduler process; workers remain outbound HTTP clients and the runtime protocol is unchanged.

## Classification

S/M/L = adapter size. "Engine-neutral" = same work as with an own scheduler.

| Requirement | Class | Notes / source |
|---|---|---|
| FR-1.1 Python DSL; FR-1.3 IR sections; FR-1.4 compile/validate | adapter (M) | Engine-neutral. |
| FR-1.2 IR consumed only by scheduler | adapter (M) | The interpreter reads the IR through a step whose output is checkpointed and evaluates it in workflow code; "a workflow function must be deterministic" ([workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial)). |
| FR-1.5 other SDKs / UI editor | adapter (S) | Works because the graph is data, not code. |
| FR-1.6 trigger rules | adapter (M) | No DAG in DBOS; the interpreter keeps a node-state table in workflow state. |
| FR-1.7 IR hash per run | native | "Every workflow input and step output is durably stored in the system database" ([architecture](https://docs.dbos.dev/architecture)). |
| FR-2.1 agent node | adapter (M) | A step that drives the attempt through the worker boundary. A step is checkpointed when it completes; no in-step progress checkpoint or heartbeat was found in the pages read. |
| FR-2.2 approval gate, no slot held | native + adapter (M) | `recv(topic, timeout)` and `get_event` are persisted; "If you're sending a message from a workflow, DBOS guarantees exactly-once delivery", from plain code `send` takes an `idempotency_key` ([communication](https://docs.dbos.dev/python/tutorials/workflow-communication)). There is no validator: a stale or malformed response is rejected by workflow code after receipt and stays in the message log. Form schema and expiry behaviour are kora. |
| FR-2.3 sensor | native + adapter (S) | Durable sleep plus a short poll step. |
| FR-2.4 timer | native | `DBOS.sleep` is durable: "DBOS saves the wakeup time in the database" ([workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial)). |
| FR-2.5 branch | adapter (S) | Deterministic code on an artifact summary. |
| FR-2.6 fan-out/fan-in, cap, skip_failures | native + adapter (S) | Child workflows on a queue with global and per-process concurrency and priority ([queues](https://docs.dbos.dev/python/tutorials/queue-tutorial)). No documented cap on child workflows was found. |
| FR-2.7 sub-graph as unit | adapter (M) | Child workflow under `SetWorkflowTimeout`, which cancels the workflow and its children; unit retry loop in the parent. |
| FR-2.7a iteration semantics | adapter (M) | Kora loop passes `previous_attempt` refs; attempt-invariant memoisation via kora DB; cross-attempt budget in workflow state. `fork_workflow` restarts a copy from a chosen step with the original inputs, which is not an attempt with new inputs ([management](https://docs.dbos.dev/python/tutorials/workflow-management)). |
| FR-2.8 command node | adapter (S) | Step. |
| FR-3.1–3.4 config, inheritance, mapping, `trust_repo_config` | adapter (M) | Engine-neutral; resolved config by reference. |
| FR-4.1 typed artifacts, lineage; FR-4.2 built-ins incl. ReviewBundle; FR-4.3 custom types | adapter (L) | No artifact model in DBOS; kora DB as planned. |
| FR-4.4 storage protocol, by-digest refs | adapter (M) | Step outputs are checkpointed in the system database, so only references cross it; sizing guidance counts writes, not bytes ([checklist](https://docs.dbos.dev/production/checklist)). |
| FR-4.5 per-attempt retention | adapter (S) | Kora DB; checkpoints keep every step output until the retention policy removes them. |
| FR-4.6 scratch vs output | adapter (S) | Loop attempts keep scratch in workflow state; a step has no partial-progress checkpoint. |
| FR-5.1–5.3 workspace, VCS protocol | adapter (M) | Inside workers; engine-neutral. |
| FR-6.1 control plane, restart resumes | native + adapter (M) | "each time you restart your application's process, DBOS recovers all workflows that were executing before the restart" ([recovery](https://docs.dbos.dev/production/workflow-recovery)). DBOS owns workflow state; kora keeps API, registry, artifact and cost DB. Two stores on one server to reconcile. |
| FR-6.1a at-most-once submission | adapter (S) | "Steps are tried at least once but are never re-executed after they complete" ([workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial)); the step persists `submission_key` before `submit` and re-looks up when executed again. Run starts dedupe by workflow id: "An assigned workflow ID acts as an idempotency key". |
| FR-6.1b side-effect boundary | adapter (S) | Policy; checkpoints record every step input and output. |
| FR-6.2 triggers | native + adapter (S) | Cron schedules: "Each time a schedule fires, its workflow is executed by exactly one worker process", with manual and automatic backfill ([schedules](https://docs.dbos.dev/python/tutorials/scheduled-workflows)); webhook = kora endpoint → `start_workflow` with the trigger key as workflow id; graph→graph = child workflow. |
| FR-6.3 retry policy fields | native + adapter (S) | Step retries: `max_attempts`, `interval_seconds`, `backoff_rate`, `should_retry` predicate ([steps](https://docs.dbos.dev/python/tutorials/step-tutorial)); no maximum interval; artifact predicates need the kora loop. |
| FR-6.4 fresh / resume / fresh_with_feedback | adapter (M) | Kora loop; resume handle from scratch state; fallback recorded. |
| FR-6.5 timeouts | native + adapter (S) | `SetWorkflowTimeout` counts from the start of execution and persists across restarts ([workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial)); no per-step timeout was found in the pages read, so the attempt timeout is enforced by the step itself. |
| FR-6.6 `max_events` | adapter (S) | Counted by the worker; the step raises a typed error that `should_retry` refuses. |
| FR-6.7 cancellation with grace | native + adapter (S) | `cancel_workflow` preempts at the start of the next step; `preemptible` async steps stop at once ([management](https://docs.dbos.dev/python/tutorials/workflow-management)); grace is the worker's code. |
| FR-6.8 manual node re-run inside a run | adapter (M) | Run stays open in "failed-awaiting" and receives a `retry_node` message; `fork_workflow` is whole-workflow from a step under a new workflow id, not per node. |
| FR-6.8a downstream invalidation | adapter (M) | Node-state table binds results to consumed versions; cancel dependent children, bump a generation so waits reject stale input. No engine help beyond cancellation. |
| FR-6.9 quotas | adapter (S) | Kora; queue rate limits are per queue and period, not per namespace. |
| FR-6.10 concurrency limits | native (per queue) + adapter (M) | Global concurrency, worker concurrency per process, rate limits "global across all DBOS processes using this queue", partitioned queues with per-partition limits ([queues](https://docs.dbos.dev/python/tutorials/queue-tutorial)). A queue per runtime and a partition per namespace map directly; per-graph limits need a kora semaphore or a partition. Deduplication is not available on partitioned queues. |
| FR-7.1 channel protocol; FR-7.2 CLI/API + VCS channels; FR-7.4 chat | adapter (M) | `request` = notification step; `notify` = `send` with an idempotency key; VCS = poll step → `send`. |
| FR-7.2a SHA-bound approval | adapter (S) | Workflow code compares `(sha, digests)` with the recorded request; a new push invalidates. |
| FR-7.3 minimal web page | adapter (S) | The console belongs to Conductor and has no forms. |
| FR-7.5 gate state as data | native | Serialisable messages and events only. |
| FR-8.1 runtime protocol | adapter (M) | Step = `submit` through the worker + poll; `adopt` = lookup by key at step start. |
| FR-8.2 local/Docker; FR-8.3 sandbox/K8s; FR-8.5 secrets; FR-8.6 network flags | adapter (M) | Workers per runtime; engine-neutral. |
| FR-8.4 per-node runtime override | native | A queue per runtime; the node's attempt is enqueued on its runtime's queue. |
| FR-8.7 enterprise runtime (in-house CI and execution platform) | adapter (M) | Unchanged from the own scheduler: the worker inside the enterprise network reaches the control plane over outbound HTTPS. The DBOS executor never leaves the control plane because it needs the system database. |
| FR-9.1 token ledger, completeness | adapter (M) | The worker reports usage over the HTTP boundary; the step writes the final record; `partial` from the last report on kill. |
| FR-9.2 price table; FR-9.3 billing class; FR-9.6 reconciliation | adapter (M) | Engine-neutral. |
| FR-9.4 soft USD budget | adapter (S) | Worker-side termination. |
| FR-9.5 cost views | adapter (M) | Workflow state + kora DB. |
| FR-10.1 secrets protocol; FR-10.2 redaction | adapter (S) | Only references cross the system database, which persists every step input and output; no payload encryption feature was found in the pages read; Conductor offers a metadata-only mode on its Teams plan. |
| FR-11.1 status/timeline | native + adapter (M) | `list_workflows`, `list_workflow_steps` and the `dbos workflow` CLI show workflows and steps, not nodes ([management](https://docs.dbos.dev/python/tutorials/workflow-management)); kora API maps. |
| FR-11.2 transcripts | adapter (S) | Outside DBOS by design. |
| FR-11.3 exporters, OTel GenAI | native + adapter (M) | `enable_otlp`, trace and log endpoints, attributes ([configuration](https://docs.dbos.dev/python/reference/configuration)); GenAI spans are kora's. |
| FR-11.4 audit log | native + adapter (S) | Checkpoints hold every step's input and output; VcsRef linkage in kora DB. |
| FR-11.5 web UI | adapter (L) | Conductor's console is a workflow view on a paid plan, not a graph view. |
| FR-12.1 components | native | kora API, scheduler process with the library, workers, PostgreSQL (two databases on one server) or SQLite. "There's no separate orchestration server and no infrastructure required besides Postgres" ([architecture](https://docs.dbos.dev/architecture)). |
| FR-12.2 CLI | adapter (M) | Run commands wrap the kora API; compile/validate/deploy/retry are kora. |
| FR-12.3 docker-compose | native | PostgreSQL + kora, as planned. |
| FR-12.4 namespaces | adapter (S) | No tenancy concept; partition keys give per-namespace queue limits. |
| FR-12.5 versioned registration | adapter (S) | Registry in kora DB; runs pin IR hash. |
| NFR-1.1 scale, days-long runs; NFR-1.4 fan-out 100 | native with a caveat | Vendor figures: "about 4 Postgres vCPUs for 1,000 actions per second" and "more than 40K workflows or steps per second on one database" ([checklist](https://docs.dbos.dev/production/checklist)). A days-long run is one workflow function recovered from checkpoints after each restart of a process on the same application version. Whether a waiting or polling workflow occupies a thread while its process is up was not stated in the pages read; verify at prototyping time. |
| NFR-1.2 stateless HA | **partial** | Processes are executors with ids; an executor recovers only its own pending workflows and applications sharing a system database "never recover each other's workflows" ([recovery](https://docs.dbos.dev/production/workflow-recovery)); "Conductor is required for correct workflow recovery in applications that use more than one process" ([FAQ](https://docs.dbos.dev/faq)). One scheduler process, as the MVP runs, is covered by restart recovery. |
| NFR-1.3 waits hold nothing | native | Sleep, `recv` and `get_event` are persisted; the saved wake-up time and the messages survive restarts. |
| NFR-2.1 durable before side effect | native + adapter (S) | Workflow inputs are checkpointed before execution; key dedupe in the step. |
| NFR-2.2 idempotent actions, trigger keys | native | Reusing a workflow id returns the existing handle by default; `workflow_id_reuse_policy="reject"` raises `DBOSWorkflowIDInUseError`. |
| NFR-2.3 heartbeat / stalled | adapter (M) | No step heartbeat was found; stall detection stays kora's, from the worker's reports over HTTP. |
| NFR-2.4 infra vs agent errors | native + adapter (S) | Infra → step retries with `should_retry`; agent → kora loop. |
| NFR-2.5 bounded waits | native | Timeouts on `recv`, `get_event`, `SetWorkflowTimeout`. |
| NFR-6.1–6.4 IR canonical, SDK is a client, migrator, file classes | adapter (S) | Preserved by the interpreter design. Cost: every workflow is tagged with an `application_version` and recovered only by a process of the same version ([FAQ](https://docs.dbos.dev/faq); [durable_execution_observability.md](../../research/agentic-pipeline-orchestrators/durable_execution_observability.md)), so an interpreter release keeps old-version executors running until their runs finish, or cancels and forks runs onto the new version. No patch-marker mechanism was found. |
| NFR-7.1 single-command standalone, SQLite | **native** | "This may be either Postgres or SQLite, though Postgres is recommended for production"; SQLite is the default when no connection string is given; polling replaces listen/notify on SQLite ([configuration](https://docs.dbos.dev/python/reference/configuration)). The only engine in this analysis whose standalone and production modes run the same code. |
| NFR-7.2 migrations; NFR-7.3 health/Prometheus; NFR-7.4 capacity per runtime | native + adapter (S) / partial / native | `dbosctl sysdb migrate` for the system database and Alembic for kora's tables: two schema tools ([checklist](https://docs.dbos.dev/production/checklist)). OTel traces and logs come from the library; a Prometheus-compatible metrics endpoint is listed as a Conductor feature ([Conductor](https://docs.dbos.dev/conductor/overview)), so kora exposes its own. Queue limits are picked up "on their next polling iteration" ([queues](https://docs.dbos.dev/python/tutorials/queue-tutorial)). |

## Hard blockers

1. **Recovery across several processes is not in the library.** Each executor recovers only its own pending workflows; a dead executor's workflows wait until Conductor (hosted and paid, or self-hosted on the Enterprise plan) signals another executor to recover them, or until kora coordinates that recovery itself. With one scheduler process, as the MVP runs, restart recovery is native. High availability is therefore a paid control plane or own code.
2. **Two state owners on one server.** Workflow checkpoints, queues and schedules live in the DBOS system database; attempts, artifacts, approvals and cost live in kora's database. A crash between a step's side effect and its checkpoint is the same reconciliation problem as under Temporal, with the system database as the authoritative source to project from.
3. **Determinism and versioning tax on the interpreter**, with version pinning instead of patch markers: a run finishes on the application version that started it, so every interpreter release keeps old executors alive until their runs end or forks the runs onto the new version. Days-long runs amplify it; users are unaffected because graphs are data.
4. **A step has no in-flight checkpoint.** A multi-hour attempt inside a step is at-least-once as a whole: adoption after a crash is kora's submission key and lookup, exactly as with the own scheduler, and heartbeats and stall detection stay kora's.

None is "cannot be done"; the first changes what high availability costs, the rest are the same costs as under Temporal in a smaller footprint.

**Not blocked after verification:** standalone mode (SQLite system database, same code); deployment footprint (nothing beyond kora's processes and PostgreSQL); enterprise runtimes (workers unchanged, executors inside); IR/UI round-trip (one fixed interpreter workflow).

## What DBOS gives for free

Durable workflow state and recovery on restart of a process; durable sleep and timers; persisted messages and events with exactly-once delivery from workflows; workflow-id idempotency with a reuse policy; queues with global and per-process concurrency, rate limits, deduplication, priority and partitions; cron schedules executed once per firing, with backfill; step retries with a predicate; workflow timeouts that cancel children; cancel, resume and fork from a step; listing of workflows and steps through the API and the CLI; OTel traces and logs; SQLite for development and PostgreSQL for production under the same code; an MIT library in Python, TypeScript, Go and Java.

## Operations and cost

**Self-hosted:** kora's own compose (PostgreSQL, scheduler, API, workers) with the library inside the scheduler process and a second database on the same PostgreSQL server; `kora standalone` is one process with the system database as a SQLite file beside kora's. "Do not use a connection pooler in transaction mode"; configure a retention policy for workflow history; if PostgreSQL is unreachable, "all DBOS applications connected to it will pause workflow execution until they reconnect" ([checklist](https://docs.dbos.dev/production/checklist)). The library is free: "Use the open source DBOS Transact library, free forever" ([pricing](https://www.dbos.dev/pricing), checked 2026-10-16).

**Conductor** (needed for recovery with more than one process; also the console, autoscaling and the metrics endpoint): the application opens a WebSocket connection with an API key after `pip install "dbos[enterprise]"`; "Conductor has no access to your application's database or other private data" and "If your connection to Conductor is interrupted, your applications will continue operating normally" ([Conductor](https://docs.dbos.dev/conductor/overview)). Prices on 2026-10-16: Pro $99/month (2 seats, 3 applications, 1M checkpoints/month, $50 per further million), Teams $499/month (10 seats, 10 applications, 10M checkpoints, $40 per further million), Enterprise custom with self-hosted Conductor ([pricing](https://www.dbos.dev/pricing)). If one checkpoint is one step or workflow write, a run shaped like the SQL optimisation graph (≈60 steps, 9 children) is ≈100 checkpoints and 10,000 runs/month ≈ 1M checkpoints/month, the volume included in the Pro plan.

## Answers to the specific questions

**IR vs workflows-as-code.** One generic interpreter workflow takes the IR as data; DSL → IR → other SDKs/UI are untouched. The console shows a flat step list, so kora still owns the node-level run view.

**Enterprise runtime.** Unchanged from the own scheduler: the adopter's worker implements the runtime protocol inside the enterprise network and reaches the control plane over outbound HTTPS. The DBOS executor stays inside the control plane because it needs the system database; the step talks to the worker, never to the enterprise platform directly.

**Cost and `max_events`.** Computed by the worker from the JSONL stream and reported over the HTTP boundary; the step writes the final `AgentSession` to kora's DB; run aggregates live in workflow state. A breach ends the attempt with a typed error that the step's `should_retry` refuses.

**Invalidation and SHA-bound approvals.** The interpreter keeps `{node: state, consumed_versions, generation}` in workflow state. An approval response arrives as a message with an idempotency key; workflow code rejects responses whose generation or `(sha, digests)` differ from the open request. Unlike a Temporal Update validator, the rejected response is still recorded in the message log.

**Standalone mode.** The one point where DBOS differs from every other engine in this analysis: the SQLite system database is the documented default, the production path is PostgreSQL, and the code is the same.

## Verdict

DBOS is the engine closest to kora's deployment shape: it adds no service, runs on the PostgreSQL kora already operates and on SQLite in standalone, and would replace the scheduler's passes (readiness, waits, timers, retries, admission, cron) with a deterministic interpreter workflow over durable queues. It does not remove kora's state model (attempts, artifacts, lineage, approvals, cost), the runtime protocol or the HTTP worker boundary, and it brings the same costs as Temporal in a smaller footprint: at-least-once steps, so adoption stays kora's; two stores to reconcile; a determinism and versioning discipline on the interpreter; and, in addition, recovery across several processes that is either a paid control plane or code kora writes. The trade is "write scheduling passes over own tables" versus "write a deterministic interpreter over the library and accept its recovery and versioning rules". Following the critique's disposition, DBOS is not adopted before the first vertical slice: the slice is the prototype of the SQL scheduler, the fault scenarios listed in the critique record are its acceptance list, and this comparison is re-evaluated when the slice has data.
