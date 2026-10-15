# Proposal 0001: Temporal-backed execution backend

- Status: deferred
- Reviewed: by the project owner, 2026-10-16
- Author: project owner, from the desk analysis of 2026-10-15
- Date: 2026-10-15
- Trigger: an adopter whose scale or reliability requirements the own scheduler does not meet after MVP, or an adopter that already operates Temporal and will not run a second scheduler. Not before the MVP ships.
- Related: [ADR-0001](../decisions/0001-own-control-plane.md), [NFR-5.5](../requirements/non-functional.md), [analysis verdict](../../knowledge/analysis/own-scheduler-vs-existing-engine/verdict.md), [Temporal analysis](../../knowledge/analysis/own-scheduler-vs-existing-engine/temporal.md), [scheduler.md section 12](../../knowledge/specs/scheduler.md), [data-model.md section 9](../../knowledge/specs/data-model.md)

## Motivation

kora runs its own scheduler: stateless passes over the metadata database that create attempts, hold waits as rows, detect stalls and apply retry policies ([ADR-0001](../decisions/0001-own-control-plane.md)). The desk analysis of Temporal, Kestra and Airflow found that only Temporal is a credible substitute: it gives durable timers and waits that hold no worker, retry policies that map one to one onto `FR-6.3`, heartbeats and stall detection, cancellation, child-workflow lifecycle, deduplication of triggers by workflow id, schedules, namespaces, per-queue routing to runtimes, an audit history, worker versioning, and the whole scale target of `NFR-1`. In exchange it adds a mandatory Temporal deployment, a second source of truth for run state, a determinism discipline on kora's interpreter, and a recurring cost (a Temporal Cloud bill or a self-hosted cluster to operate).

The owner decided to keep the own control plane for MVP and to keep the Temporal option open by design rather than by promise: run state is plain interpretable data with explicit attempt objects and waits as rows, so an execution backend can replay or own it (`NFR-5.5`). This proposal records how the option would be exercised.

## Proposal

Add an execution backend behind the boundary described in [scheduler.md section 12](../../knowledge/specs/scheduler.md): the part of the scheduler that advances a run (readiness, admission, waits, timeouts, completion and retry) becomes a Temporal workflow per run, while kora keeps ownership of everything Temporal has no object for.

- **One fixed workflow definition** interprets the compiled graph document as data. The workflow code never changes per graph; graph changes are data changes and need no workflow versioning. Kora's interpreter inside the workflow must be deterministic: it reads the document and the run's inputs from workflow arguments, never from the database directly.
- **One activity per attempt.** An attempt is a Temporal activity with heartbeats carrying the session's event counter and estimated spend; `max_events`, the soft budget and `stall_after` stay enforced by the session runner inside the runtime ([ADR-0004](../decisions/0004-harness-adapters-over-vendor-sdks.md)); the activity's heartbeat timeout is the transport-level stall detector. At-most-once submission keeps using the attempt id as the submission key; Temporal's task token replaces the lease epoch for fencing.
- **Waits** become durable timers (`timer`), signals (`wait_event`, webhooks delivered by the API as signals) and updates with validators (`human_input`: the validator checks the commit SHA and the fact digests the approval binds to, so [ADR-0008](../decisions/0008-content-bound-approvals.md) is preserved).
- **Sub-graph attempts** become child workflows; `previous_attempt` and `fail_attempt` are passed as child-workflow arguments and results.
- **Fan-out** maps to child workflows per element with the collected list as the parent's result.
- **What stays in kora's database**: graphs and versions, presets, artifacts and their versions, usage records and cost, approval requests and responses, namespaces, tokens, retention. Temporal history holds run progress; kora's `run`, `node_instance` and `attempt` rows become a projection written by the workflow through activities, so every API read keeps working unchanged.
- **Surfaces do not change.** The HTTP API, CLI, MCP tools, skills and web console are unaffected; the backend is a deployment choice (`kora scheduler --backend temporal`).

## Alternatives

- **Kestra as the engine.** Rejected by the analysis: retries re-render identical inputs, so the attempt model with `previous_attempt` cannot be expressed; Pause is resume-or-kill, so content-bound approvals cannot be enforced; plugins are Java; routing to runtimes needs the enterprise edition.
- **Airflow as the engine.** Rejected: XCom is cleared on retry and there is no task-group-level retry, so attempt lineage and sub-graph iteration conflict with the engine; human-in-the-loop is bound to the task instance and the UI button bypasses checks; the scheduler consumes Python DAG files, which conflicts with the document-first design. Airflow stays an integration target: a DAG starts kora runs through the webhook trigger, and a dedicated operator can follow after MVP ([ADR-0001](../decisions/0001-own-control-plane.md)).
- **Keep the own scheduler and scale it.** The default. The analysis found the own scheduler's work to be roughly a third of the reliability and scheduling effort plus the scale work; if the MVP meets `NFR-1` on Postgres, this proposal is never triggered.

## Plan

1. Prove the boundary on the own scheduler first: the interpreter that advances a run must already take the graph document and run state as arguments and return transitions, without side effects, so that the same code runs inside a Temporal workflow unchanged.
2. Implement the Temporal backend behind the boundary with the fake harness and fake runtime (`NFR-10.1`), using the Temporal development server in tests.
3. Run the canonical SQL optimisation graph on both backends and compare the attempt rows, artifacts, approval requests and usage records; they must be identical except for timestamps and backend identifiers.
4. Document operations: the Temporal deployment beside kora's database, reconciliation when the two disagree, upgrade of the interpreter under Temporal's versioning rules.

## Risks and open questions

- Two stores to reconcile: Temporal history and kora's database can disagree after a partial failure; the projection must be idempotent and repairable from history.
- Standalone mode has no production-grade single-process Temporal; the Temporal backend is for the organisation deployment shape only, and `NFR-0.1` (nothing exists only in standalone) applies in reverse: nothing may exist only on the Temporal backend.
- Determinism: any non-deterministic code path in the interpreter (time, random, database reads) breaks replay; the boundary in step 1 of the plan is the guard.
- Cost at the owner's target scale is an operating expense the own scheduler does not have.

## Decision

Deferred on 2026-10-15 by the project owner: own control plane for MVP; the run-state model keeps this backend possible ([ADR-0001](../decisions/0001-own-control-plane.md), `NFR-5.5`). To be revisited when the trigger above occurs.
