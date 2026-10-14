# H3 analysis: kora as a provider package on Apache Airflow 3.3

Status: desk analysis, 2026-10-15. Tests hypothesis H3 of `positioning.md` for the Airflow option only. Sources: kora FR/NFR v0.3, `research_notes/agentic-pipeline-orchestrators/*`, and official Airflow 3.3.2 docs fetched today (URLs inline). No code was written.

## Adapter model under evaluation

`apache-airflow-providers-kora`: `AgentSessionOperator` (subprocess `claude -p`/`codex exec`, streamed JSONL, kora-counted `max_events`, soft budget, typed output → XCom + state store); command nodes = `BashOperator`/`DockerOperator`/KPO; sub-graph = TaskGroup or `TriggerDagRunOperator` child run; approval = HITL operators + notifier + a kora channel that PATCHes `hitlDetails`; sensors/timers = deferrable; fan-out = `.expand()`; artifacts = custom XCom backend + object store + kora tables; DSL = wrapper over `@dag`/`@task`; runtime = `executor=`/`executor_config` or KPO; enterprise runtime = custom executor or operator-level submitter.

Classes: **native** (Airflow does it), **adapter S/M/L** (kora code on top, no Airflow change), **blocked** (an adapter cannot remove the gap).

## Classification

| Req | Class | Notes / source |
|---|---|---|
| FR-1.1 | native | TaskFlow `@dag`/`@task`, `>>`, XComArg deps. [tasks](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html) |
| FR-1.2 | **blocked** | "Airflow loads Dags from Python source files in Dag bundles"; re-parsed every 30 s; the `ExecuteTask` workload carries `dag_rel_path`+`bundle_info`, so workers re-import Python too. The IR can only feed a generated loader (DAG Factory pattern). [dags](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html), [executor](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/executor/index.html), [config](https://airflow.apache.org/docs/apache-airflow/stable/configurations-ref.html) |
| FR-1.3 | adapter S | kora's own schema; Airflow has no separate policy/secret-ref sections. |
| FR-1.4 | adapter M | `kora compile/validate` stay kora's; artifact-type edge checks have no Airflow equivalent. |
| FR-1.5 | adapter M | Serialized DAG JSON is "a versioned contract between Task SDKs and Airflow server components", not an authoring format (see verdict). [serialization](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/dag-serialization.html) |
| FR-1.6 | native | 13 trigger rules incl. all six required. [dags](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html) |
| FR-1.7 | native | DAG versioning; "Each DAG run records the bundle version"; clear/rerun default to original (`rerun_with_latest_version=False`). Needs `GitDagBundle`; `LocalDagBundle` is unversioned. [bundles](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/dag-bundles.html) |
| FR-2.1 | adapter L | No local-harness operator; `AnthropicAgentSessionOperator` 1.2.0 is Managed-Agents-only ("the worker only orchestrates"), returns the session id, and notes "Airflow clears XCom each attempt, so usage reflects only the final attempt". A subprocess session pins a worker slot for hours; deferral needs an async trigger that cannot own a local subprocess. [anthropic](https://airflow.apache.org/docs/apache-airflow-providers-anthropic/stable/operators/anthropic.html), [deferring](https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/deferring.html) |
| FR-2.2 | native / adapter S | `HITLOperator`/`ApprovalOperator`: `params`, `defaults` on `response_timeout`, `assigned_users`, `notifiers`; 3.3 `awaiting_input` "uses neither a worker slot nor the triggerer". Typed-schema enforcement server-side is not documented. [hitl](https://airflow.apache.org/docs/apache-airflow/stable/tutorial/hitl.html) |
| FR-2.3 | native | Deferrable sensors / `reschedule` mode, `timeout`; `AirflowSensorTimeout` is non-retryable. |
| FR-2.4 | native | `TimeDeltaSensorAsync`/`DateTimeSensorAsync` on the triggerer. |
| FR-2.5 | native | `@task.branch`, `BaseBranchOperator`; routing code runs on the worker. |
| FR-2.6 | native / adapter S | `expand`, mapped `@task_group`, lazy fan-in, `max_active_tis_per_dag` cap, `max_map_length` 1024. `skip_failures` = trigger rule + filter. [mapping](https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/dynamic-task-mapping.html) |
| FR-2.7 | **blocked** (TaskGroup) / adapter M (child DAG) | TaskGroup carries only `default_args`: no group-level retry, timeout or budget. `TriggerDagRunOperator` + `wait_for_completion` retried N times = N child runs, but outputs cross a run boundary (XCom pull by `dag_id`+`run_id`, or experimental `/wait`). [dags](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html), [dag-result](https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/dag-result.html) |
| FR-2.7a | adapter L | A kora `SubgraphOperator` assembles `previous_attempt`: deterministic child `run_id` per try, previous artifacts from kora tables (parent XCom is cleared), handle in the state store; attempt-invariant inputs via `conf`. Lineage lives only in kora tables. |
| FR-2.8 | native / adapter S | `BashOperator`, `DockerOperator`, KPO; workspace checkout semantics are kora's wrapper. |
| FR-3.1 | adapter M | Operator parameters + compile-time validation. |
| FR-3.2 | adapter M | `default_args` (DAG, TaskGroup) and `client_defaults` precedence exist, scalar override only; list-append/deep-merge and presets are resolved by kora at compile time. [serialization](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/dag-serialization.html) |
| FR-3.3 | adapter L | Harness adapter is kora code in either design. |
| FR-3.4 | adapter S | Operator flag. |
| FR-4.1 | adapter L | XCom key = (`key`,`task_id`,`dag_id`,`run_id`,`map_index`); no type name, lineage or attempt. Custom `BaseXCom` backend stores a kora artifact id; metadata in kora tables. [xcom](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/xcoms.html) |
| FR-4.2 | adapter M | Pydantic payloads via custom serializer; `ReviewBundle` aggregation (cost of all attempts, attempt history) needs kora tables. |
| FR-4.3 | adapter S | Entry-point registry. |
| FR-4.4 | native / adapter M | `XComObjectStorageBackend` (common-io) for payloads; metadata protocol is kora's. |
| FR-4.5 | **blocked** natively / adapter M | "On every retry task XComs will be cleared"; history keeps "only the task instance attributes, including the logs". Retention needs a no-op `purge` backend plus kora rows keyed by `try_number`; Airflow UI shows the last attempt only. [xcom](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/xcoms.html), [dag-run](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dag-run.html) |
| FR-4.6 | native | Exact match: XCom (cleared) vs task state store ("survives worker crashes and task retries"), 64 KB default per value, `clear_on_success=False`. [state store](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/task-state-store.html) |
| FR-5.1–5.3 | adapter M | No workspace/VCS notion (GitDagBundle is for DAG code). Same work in both designs. |
| FR-6.1 | native | Scheduler, DB, API server, triggerer, dag processor, executors. |
| FR-6.1a | adapter M | Airflow is at-least-once. KPO `durable=True` is the pattern: persist the handle in the state store before waiting, reattach on retry; `submission_key` = `ti.id`+`try_number`. A heartbeat-timeout failure still increments `try_number`. [kpo](https://airflow.apache.org/docs/apache-airflow-providers-cncf-kubernetes/stable/operators.html), [astronomer](https://www.astronomer.io/blog/durable-execution-with-apache-airflow.md) |
| FR-6.1b | adapter S | Airflow clear never deletes external state. |
| FR-6.2 | native | Manual/API (`POST dagRuns`, JWT), cron, assets + `AssetWatcher`/`MessageQueueTrigger`, `TriggerDagRunOperator`. Secret-authenticated webhook = thin endpoint. |
| FR-6.3 | native | `retries`, `retry_delay`, exponential backoff, `max_retry_delay`, AIP-105 `ExceptionRetryPolicy`/`RetryRule` (RETRY/FAIL/DEFAULT, custom `evaluate()`). Artifact predicates = typed exceptions. [tasks](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html) |
| FR-6.4 | adapter M | Session handle from state store; previous artifacts from kora tables (XCom gone). |
| FR-6.5 | native | `execution_timeout` (total incl. deferrals), `dagrun_timeout`, sensor `timeout`, HITL `response_timeout`, Deadline Alerts (experimental). |
| FR-6.6 | adapter M | kora counts JSONL events; conflicts with deferral (trigger must tail the stream), so enforcement moves into an in-runtime session runner. |
| FR-6.7 | native / adapter S | `on_kill`, KPO `termination_grace_period`, `on_kill_action`. |
| FR-6.8 | native | Clear with downstream / `only_failed`, CLI and REST `clearTaskInstances`. |
| FR-6.8a | adapter M, partly blocked | Clear-downstream is manual and task-based, not artifact-version-based; no per-input opt-out; superseded XCom is not kept. kora must drive clears from its artifact-version table. |
| FR-6.9 | adapter M | kora quota table + pause DAG via API. |
| FR-6.10 | native | `parallelism`, `max_active_tasks`, `max_active_runs`, pools (`include_deferred`), per-executor slots. |
| FR-7.1 | adapter M | Outbound = `notifiers`; inbound = `PATCH .../hitlDetails`; pending list via `GET /hitlDetails?response_received=false`. |
| FR-7.2 | native (CLI/API) / adapter M (VCS) | VCS channel = kora poller that PATCHes the HITL record. |
| FR-7.2a | **blocked** natively / adapter M | HITL responses bind to a task instance, not a SHA/digests; a push does not invalidate. kora can store (ti, SHA, digests) and clear on push, but the UI Approve button bypasses it unless `assigned_users` is a kora service user. |
| FR-7.3 | native | "Required Actions" UI. |
| FR-7.4 | adapter M | Notifiers exist outbound (Slack etc.); inbound is kora. |
| FR-7.5 | native (3.3.1+) | CVE-2026-67260 fixed; HITL details are JSON rows. |
| FR-8.1 | adapter M / mismatch | `BaseExecutor` verbs match (`execute_async`, `sync`, `try_adopt_task_instances`, `terminate`, `get_task_log`), but an executor runs the Task SDK supervisor (Airflow installed, bundle present), not a session; kora's runtime sits at operator level, so its protocol exists anyway. [task-sdk](https://airflow.apache.org/docs/task-sdk/stable/concepts.html) |
| FR-8.2 | adapter M | `LocalExecutor` holds a slot per session; `DockerOperator` has mounts, env, `network_mode`, `private_environment`, no deferral, no reattach. [docker](https://airflow.apache.org/docs/apache-airflow-providers-docker/stable/_api/airflow/providers/docker/operators/docker/index.html) |
| FR-8.3 | native (K8s) / adapter M | KPO: `durable` reattach, deferrable, grace period; cloud sandboxes = kora. |
| FR-8.4 | native | `executor=` per task, `executor_config`. |
| FR-8.5 | native / adapter S | Secrets backends → connections; KPO `secrets=`. |
| FR-8.6 | adapter M | K8s NetworkPolicy / Docker `network_mode`; capability flags are kora's. |
| FR-8.7 | adapter L | Arcadia/Sandbox as a custom executor needs Airflow + bundle + HTTPS to the Execution API inside Sandbox (Edge3 model); operator-level submit/poll is simpler and keeps the kora protocol. [edge3](https://airflow.apache.org/docs/apache-airflow-providers-edge3/stable/index.html) |
| FR-9.1–9.3 | adapter M | No cost model in Airflow (common-ai "AIBudget" is future work); per-attempt usage must bypass XCom. |
| FR-9.4 | native (timeouts) / adapter M (events, USD) | |
| FR-9.5–9.6 | adapter M | No UI aggregation; kora CLI/API over kora tables. |
| FR-10.1 | native | Secrets backends (env, local file, Vault, K8s, cloud). |
| FR-10.2 | native / adapter S | Secrets masker for connections/variables; transcript redaction is kora's. |
| FR-11.1 | native | CLI, REST v2, UI; per-try task logs. |
| FR-11.2 | adapter M | Logs are retained per try (free), but the transcript as an artifact reference is kora's. |
| FR-11.3 | native / adapter M | `[traces] otel_on`; custom spans nest under the task span; GenAI conventions, Langfuse, Datadog = kora. OpenLineage is free. [traces](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/logging-monitoring/traces.html), [openlineage](https://airflow.apache.org/docs/apache-airflow-providers-openlineage/stable/guides/structure.html) |
| FR-11.4 | native / adapter M | API audit log, HITL responder; run→attempt→session→VcsRef linkage is kora's. |
| FR-11.5 | native / blocked (retry diff) | Grid/graph/logs/XCom UI; no per-try XCom to diff. |
| FR-12.1 | native | Postgres 13–17, MySQL 8; SQLite "should NEVER be used for production". [db](https://airflow.apache.org/docs/apache-airflow/stable/howto/set-up-database.html) |
| FR-12.2 | native / adapter M | trigger/runs/logs/clear/cancel/approve native; compile/validate/deploy = kora (deploy = commit loader into a bundle; refresh 300 s). |
| FR-12.3 | native | `airflow standalone`; official docker-compose; Helm chart. [start](https://airflow.apache.org/docs/apache-airflow/stable/start.html) |
| FR-12.4 | native, experimental | Multi-team is "experimental ... still in preview", "not yet complete (3.4+)": team-owned bundles, team-scoped connections/variables/pools/XCom, shared scheduler and DB. Single token via JWT. [multi-team](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/multi-team.html) |
| FR-12.5 | native | DAG versioning with GitDagBundle. |
| NFR-1.1 | native with caveat | Proven scale, but hundreds of hour-long sessions need Celery/K8s workers or KPO+deferral; local subprocess sessions pin worker slots. |
| NFR-1.2 | native | Multiple schedulers over Postgres. |
| NFR-1.3 | native | `awaiting_input`, `deferred`, `reschedule`. |
| NFR-1.4 | partly **blocked** | "It is not currently permitted to do task mapping nested inside a mapped task group"; workaround = mapped triggered child DAGs. |
| NFR-2.1 | adapter M | See FR-6.1a. |
| NFR-2.2 | native | Unique `dag_run_id` dedupes triggers. |
| NFR-2.3 | native / adapter S | Heartbeat timeout (default 300 s) detects dead processes; stalled-agent-in-healthy-container detection is kora's. |
| NFR-2.4 | adapter M, partly blocked | Heartbeat-timeout failures consume `retries`; AIP-105 keys on task exceptions, not infrastructure loss. KPO `durable` softens this. |
| NFR-2.5 | native | `awaiting_input` is DB state; triggers redistribute; 3.3.1 fails unresumable deferred tasks instead of hanging. |
| NFR-6.1 | adapter M | kora IR stays canonical; see verdict. |
| NFR-6.2 | **blocked** as written / adapter M | Source of truth for Airflow is the Python file; kora becomes Python → IR → generated Python. Deterministic compile is fine. |
| NFR-6.3–6.4 | adapter S | |
| NFR-7.1 | native | `airflow standalone`; compose. |
| NFR-7.2 | native / adapter S | `airflow db migrate`; kora tables need their own migrations (separate schema). |
| NFR-7.3 | native | `/health`, StatsD/OTel metrics, executor slot metrics. |
| NFR-7.4 | native | Pools editable at runtime; `executor.open_slots/queued_slots/running_tasks`. |

## Hard blockers (an adapter cannot remove)

1. **Scheduler re-parses Python DAG files.** FR-1.2/NFR-6.2 invert: the IR feeds a generated loader instead of being what runs.
2. **No TaskGroup-level retry/timeout/budget.** Sub-graph retry only as a child DAG run, which splits one kora run into N Airflow runs and pushes attempt lineage, `previous_attempt` and cross-run artifact passing entirely into kora tables.
3. **XCom is cleared on retry and not kept in history.** Per-attempt artifacts, retry diffs, per-attempt cost and `fresh_with_feedback` cannot use Airflow's data path; kora needs its own artifact and usage tables beside the metadata DB.
4. **Approvals are not content-bound.** HITL records attach to a task instance; the UI Approve button cannot be made to check a SHA.
5. **Streamed session events are not a first-class object.** Only per-try logs; deferral requires an async trigger, so a session must run outside the worker (container/sandbox) to be durable, which means Airflow's own executors are not kora's runtime.
6. **No nested fan-out inside mapped groups.**
7. **Operational footprint.** API server, scheduler, dag processor, triggerer, Postgres, executor workers (Celery+broker or K8s), a git bundle, plus kora's tables and object store; SQLite is dev-only; one 3.3.0 HITL CVE already.

## What Airflow gives for free

Scheduler and HA, cron/asset/event/API triggers, all trigger rules, dynamic mapping with mapped task groups, `awaiting_input` HITL with notifiers and Required Actions UI, deferrable sensors and timers, AIP-105 retry policies, timeouts and Deadline Alerts, pools and concurrency, DAG versioning via bundles, task state store (scratch vs output split, exactly FR-4.6), KPO `durable` reattach, secrets backends, OTel traces with task-nested custom spans, OpenLineage, REST v2 with JWT, CLI, UI, `airflow standalone`, compose and Helm, experimental multi-team.

## Answers to the specific questions

**What the user operates.** API server, scheduler, dag processor, triggerer, executor workers, Postgres, a DAG bundle, and kora's side-store (artifacts, attempts, usage, SHA-bound approvals). Heavier than `kora standalone`; `airflow standalone` on SQLite is not production.

**Long-running non-deterministic tasks.** Pre-3.3: a crash at minute 40 restarted at 0, retries re-paid LLM calls, HITL held the triggerer. 3.3's state store, `durable` KPO, `awaiting_input` and retry policies cover handle persistence and zero-slot waits. Unchanged: XCom reset, at-least-once execution, worker slot pinned by a local subprocess, and common-ai `durable` excludes streaming and HITL review. Either design needs an in-runtime session runner (checkout, harness, event counting, budget, usage).

**Serialized DAG JSON as kora IR.** No: it is the output of parsing, "a versioned contract between Task SDKs and Airflow server components", carries operator class paths, lacks artifact types, presets and secret references, and still requires a Python file in a bundle. DAG Factory (YAML → Python loader) is the viable direction; kora's IR stays canonical and is what a UI would edit.

**Multi-team in 3.3 OSS.** Exists, experimental, incomplete until 3.4+; logical isolation only (shared scheduler and DB). Enough for FR-12.4 namespaces.

**Cost accounting.** Feasible only in kora tables: XCom holds the last attempt, Airflow has no cost model (common-ai AIBudget is future work), and mid-session budget stops need the event stream that deferral hides.

## Verdict

About half of the requirements are native or small adapters: scheduling, waiting, retries, concurrency, secrets, observability, deployment. Everything that makes kora different (IR-first definition, sub-graph retry with attempt lineage, per-attempt typed artifacts, content-bound approvals, streamed session events with budgets, per-attempt cost, enterprise runtime) lands outside Airflow's primitives and needs kora-owned tables, storage, channel logic and a runtime protocol. The result is not "kora as a library over Airflow" but "kora's control plane beside Airflow, with Airflow as scheduler", at higher operational cost than kora standalone. H3 for Airflow: the limitation list is not short. Build the own scheduler, borrow Airflow's designs (trigger rules, XCom/state-store split, `awaiting_input`, AIP-105, `client_defaults` precedence), and ship an Airflow integration (trigger kora graphs from Airflow; a single-node `AgentSessionOperator`) instead of a provider-based core.
