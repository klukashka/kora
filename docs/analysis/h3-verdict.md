# H3 verdict: existing engine + session adapter vs own control plane

Status: desk analysis, 2026-10-15. Method: three independent reviews of kora's requirements (`docs/functional-requirements.md`, `docs/non-functional-requirements.md`) against the official documentation of one engine each, modelling kora as a thin adapter on top of it. No prototypes were built. Per-engine detail with requirement-by-requirement classification and sources: [h3-temporal.md](h3-temporal.md), [h3-kestra.md](h3-kestra.md), [h3-airflow.md](h3-airflow.md).

Hypothesis H3 (from `docs/positioning.md`): *an own scheduler is necessary; an existing engine plus a session adapter would not suffice.*

## Summary

| | Temporal | Kestra 2.0 (OSS) | Airflow 3.3 |
|---|---|---|---|
| Share of kora requirements native or small adapter | ~1/3 (reliability, scheduling, waits, scale) | ~1/3 (triggers, pause, subflow retry, fan-out) | ~1/2 (triggers, trigger rules, mapping, HITL, deferrable waits, retry policies, pools, secrets, OTel) |
| Attempt model (`previous_attempt`, per-attempt artifacts, lineage) | adapter: kora loop inside the workflow interpreter | **blocked**: retries re-render identical inputs, attempts hold only state/log | **blocked**: XCom cleared on retry, no TaskGroup-level retry |
| Conditional retry by error class (`retry_on`, budget vs infra) | native (RetryPolicy, non-retryable types) | **blocked**: unconditional retries | native (AIP-105) |
| At-most-once submission, adoption after crash | native (activity semantics, task tokens) | **blocked**: worker restart re-runs, no adopt | partial (adopt exists, at-least-once semantics, heartbeat timeouts consume retries) |
| Approval bound to commit SHA, invalidation | adapter (Update validators) | **blocked**: Pause is resume-or-kill | **blocked**: HITL bound to task instance, UI button bypasses checks |
| Streamed session events, `max_events`, mid-session budget | adapter inside the Activity | adapter inside the task, cannot steer retry | adapter; local subprocess pins a worker slot for hours unless run via durable KPO |
| Enterprise runtime (Arcadia / Sandbox / Tasklets) | adapter: worker polls out, async completion | Java `TaskRunner` plugin; routing needs EE Worker Groups | operator-level submitter; custom executor impractical |
| YAML IR as canonical, other-language SDKs, UI editing | compatible: one fixed workflow interprets the IR as data | Kestra YAML is not a sufficient IR; lowering step needed | **blocked**: scheduler consumes Python DAG files; serialized JSON is not an IR |
| Standalone single-process mode (SQLite) | **blocked** for production (dev-server only) | **blocked** (JVM + Postgres/MySQL) | `airflow standalone` exists; SQLite never for production |
| Plugin language | Python | Java only | Python |
| OSS/EE boundary affecting kora | server MIT; Cloud optional | K8s and cloud runners, secrets managers, RBAC, audit, worker groups are EE | multi-team experimental in OSS |
| Operational footprint added | Temporal services + Postgres + UI (+ codec server) beside kora's own DB | JVM server/workers + Postgres beside kora state | API server, scheduler, dag processor, triggerer, workers, Postgres, bundles beside kora state |
| Reviewer's verdict | viable; does not remove kora's state model; two stores to reconcile | not a thin adapter; "orchestrator inside an orchestrator" | "control plane beside Airflow", not a library over it |

## What is engine-neutral

Every review found the same set of work that kora must write regardless of engine, because no engine has these objects: the Python DSL and YAML IR with layered presets; the artifact model (`StructuredOutput`, `VcsRef`, `AgentSession`, `CommandResult`, `ReviewBundle`) with per-attempt retention and lineage; the harness adapters (JSONL streaming, `max_events`, soft budget, usage completeness); the VCS, runtime, secrets and approval-channel protocols; the cost ledger and price table; the CLI and the approvals page. This is the larger part of kora by volume, and it is the part that differentiates it.

## What an engine would give for free

Temporal: durable timers and waits that hold no worker, retry policy fields that map 1:1 onto FR-6.3, heartbeats and stall detection, cancellation, child-workflow lifecycle, workflow-id dedupe for triggers, schedules, namespaces, per-queue runtime routing, audit history, worker versioning, Prometheus/OTel, and the entire NFR-1 scale target. Airflow: triggers, trigger rules, dynamic mapping, `awaiting_input` HITL, deferrable waits, AIP-105 retry policies, pools, DAG versioning, secrets backends, OTel/OpenLineage, REST v2, UI and CLI. Kestra: scheduler, triggers, `Pause` with typed `onResume`, subflow retry-as-new-execution, loops, concurrency, revisions, internal storage, Process/Docker runners, API and SDKs.

## Blockers in common

1. **No engine owns the attempt as an object.** `previous_attempt` inputs, per-attempt artifacts, retry diffs, per-attempt cost and lineage live in kora's own tables under every option. With Kestra and Airflow this is a hard conflict with the engine's retry model; with Temporal it is a loop kora writes inside the workflow.
2. **Approvals bound to content** (commit SHA plus gate digests, invalidation on new push, cancellation of superseded approvals) exist nowhere; Temporal's Update validators can express them, Kestra and Airflow cannot without bypass risk.
3. **Standalone mode** (one process, SQLite, for development and for the single-engineer persona) is not available in production form on any engine.
4. **Enterprise runtime** (Arcadia CI / Sandbox / Tasklets, arc VCS) is reachable only through adapter code in all three; Kestra additionally gates routing behind EE.

## Verdict

Kestra and Airflow fail H3 in the direction the hypothesis predicted: the limitation list is long, and the result would be kora's control plane *beside* the engine, with higher operational cost and a Java or Python-file-based plugin surface that conflicts with the IR-first design. Both remain integration targets (trigger kora from them, expose a single-node operator/task), and Airflow's primitives (trigger rules, XCom vs state-store split, `awaiting_input`, AIP-105, `client_defaults` precedence) are worth borrowing.

Temporal is the one credible alternative. Its blockers are operational, not semantic: a mandatory Temporal deployment with no production single-process mode, a second source of truth (Temporal history for run state, kora DB for artifacts, cost, approvals and registry) that must be reconciled, a determinism and versioning discipline on kora's interpreter, and at target scale a Temporal Cloud bill in the low thousands of dollars per month or a self-hosted cluster. In exchange it removes roughly a third of the reliability and scheduling work and all of the scale work.

H3 is therefore **partly confirmed**: an own scheduler is not strictly necessary, but no engine removes kora's state model, and only Temporal avoids fighting kora's retry and approval semantics. The decision reduces to one trade: *write a scheduler and state model* versus *operate Temporal, keep two stores in sync, and give up standalone mode*. The owner decides; the recommendation of this analysis is to keep the own control plane for MVP, design the scheduler's run-state model so that a Temporal-backed execution backend remains possible later (run state as an interpretable IR, attempts as explicit objects, waits as rows), and ship Airflow and Kestra integrations rather than cores.
