# ADR-0001: Own control plane, with a Temporal-backed execution backend kept possible

- Status: accepted, 2026-10-15
- Deciders: project owner, after the desk analysis of existing engines and the design review of 2026-10-15
- Absorbs: DD-7, requirements decisions 6 and 12, NFR-5.5
- Related: ADR-0003, ADR-0007, ADR-0008, ADR-0013

## Context

kora runs graphs whose nodes are whole coding-agent sessions, and the requirements ask for a control plane from day one: scheduler, metadata database, HTTP API, workers (FR-6.1). Writing a scheduler is expensive, and mature engines already provide retries, durable waits, cron and human-in-the-loop steps. The main alternative was "an existing engine plus a session adapter": one coding session as one Temporal activity, Kestra task or Airflow task, with the engine owning retries, waits and approvals. The positioning document recorded this as the hypothesis "own scheduler is necessary" and required a test before scope freeze (requirements decision 6).

The test was a desk analysis of Temporal, Kestra 2.0 and Airflow 3.3 against kora's requirements, modelling kora as a thin adapter on each ([verdict](../../knowledge/analysis/own-scheduler-vs-existing-engine/verdict.md)); no prototype was built. Kestra and Airflow fail: neither owns an attempt as an object, so `previous_attempt` inputs and per-attempt artifacts conflict with their retry models; approvals cannot be bound to a commit SHA and gate digests without bypass risk; neither has a production standalone mode; Airflow consumes Python DAG files rather than an intermediate representation (IR), and Kestra's plugins are Java only. Temporal is viable: it covers roughly a third of the requirements natively and its Update validators can express content-bound approvals, but it forces a mandatory deployment with no single-process mode, a second source of truth beside kora's database and a determinism discipline on kora's interpreter. Every engine leaves the same engine-neutral work: DSL and IR, artifact model with lineage, harness adapters, protocols, cost ledger. The hypothesis was partly confirmed: an own scheduler is not strictly necessary, but no engine removes kora's state model.

## Decision

kora keeps its own control plane for the MVP (requirements decision 12): a scheduler that is a stateless loop over the metadata database, an HTTP API, and workers per runtime (ADR-0003).

The run-state model is designed so that a Temporal-backed execution backend stays possible (NFR-5.5). "Run state as interpretable data with explicit attempts" means:

- Every `run`, `node_instance`, `attempt` and `wait` row is plain JSON-compatible data: no pickled objects, no callables. The scheduler is a pure function from rows to actions and keeps no in-memory state between passes (NFR-1.2).
- Each runtime submission is an explicit `attempt` row with its own status, lease, counters, artifact versions and usage records (ADR-0007), the object no engine owns.
- Timers, approvals, event subscriptions, polls, retry back-off and admission back-pressure are `wait` rows with a `wake_at` or a subscription; nothing holds a worker.
- The backend boundary is prose. The scheduling passes are the only code that moves the four execution tables between states, reached through three entry points: start a run, deliver a signal (approval response, inbound event, cancel, manual retry) and tick. The design review of 2026-10-15 ([record](../../knowledge/reviews/review-2026-10-15.md)) found that an `ExecutionBackend` Python interface with one implementation adds nothing, so none exists (DD-7).

The Temporal backend is prototyped after MVP, by demand (DD-7). A generic interpreter workflow would then replace the passes and project its transitions into the same four tables, while artifacts, lineage, approval requests, usage records, session segments, events and the registry stay in kora's database, written through the same repository layer; readers always read kora's tables. Two rules keep the door open: no table stores backend-specific handles except `attempt.handle` and `attempt.lease_*`, and the scheduler never relies on in-memory state.

Kestra and Airflow become integration targets only: trigger kora from them, expose a single-node operator or task.

## Alternatives considered

- **Temporal as the core from the start.** Rejected for MVP: a mandatory cluster or Temporal Cloud bill, no standalone mode (NFR-7.1, NFR-0.1), run state split across two stores. Kept as the one credible alternative backend.
- **Kestra or Airflow as the core.** Rejected: the attempt model, content-bound approvals and the IR-first design are blocked; the result is an orchestrator inside an orchestrator.
- **A coded `ExecutionBackend` interface now.** Rejected as an abstraction with one implementation.

## Consequences

- kora owns scheduling, leases, adoption after crash, stall detection and scale, work Temporal would have given for free.
- Standalone mode on SQLite keeps the same schema and queries as PostgreSQL (NFR-0.1).
- A future backend inherits the data model unchanged; nothing in the API or the protocols depends on which backend performs transitions.

## Where it is applied

- [scheduler.md §2](../../knowledge/specs/scheduler.md) (the passes) and [scheduler.md §12](../../knowledge/specs/scheduler.md) (execution backend boundary)
- [data-model.md §1](../../knowledge/specs/data-model.md) (principles) and [data-model.md §9](../../knowledge/specs/data-model.md) (compatibility with a Temporal-backed backend)
- [verdict.md](../../knowledge/analysis/own-scheduler-vs-existing-engine/verdict.md) with the per-engine detail beside it
- [NFR-5.5, NFR-1.2, NFR-0.1](../requirements/non-functional.md)
- [FR-6.1](../requirements/functional.md) and [functional.md §13, decisions 6 and 12](../requirements/functional.md)
- [positioning.md, "Evidence, hypotheses and open risks"](../positioning.md)
- [architecture detailed.md §2](../../knowledge/architecture/detailed.md)
