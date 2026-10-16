# ADR-0007: Explicit attempts with lineage, sub-graph iteration semantics, at-most-once submission and retention

- Status: accepted, 2026-10-15; amended 2026-10-16 (result authority revoked by clear-downstream; [architecture critique of 2026-10-16, finding A06](../../knowledge/reviews/architecture-critique-2026-10-16.md#a06-result-authority), issue #13)
- Reviewed: by the project owner, 2026-10-16
- Deciders: project owner, after an external review of the requirements and the design review of 2026-10-15
- Absorbs: requirements decisions 2, 3, 8, 9 and 10 (the downstream-invalidation part), DD-4
- Related: ADR-0001, ADR-0003, ADR-0008, ADR-0010

## Context

A coding-agent session is non-deterministic and expensive. A run that retries a node or re-iterates a sub-graph produces several candidates for the same output, each with its own cost, transcript and branch. The canonical graph loops (implement, measure, check equivalence, review) and a rejected attempt must pass its comments and branch to the next one; the graph stays acyclic, so a loop is a retry with memory.

The desk analysis of existing engines found that none owns the attempt as an object (ADR-0001). Session side effects are not transactional, and a crash between submit and handle persistence must not start a second session (NFR-2.1). The design review of 2026-10-15 ([record](../../knowledge/reviews/review-2026-10-15.md)) simplified the first drafts: "attempt" no longer also means a sub-graph iteration, a transactional clear-downstream replaces generation counters and a dirty set, and retention leaves the scheduling loop.

## Decision

**Every execution is an attempt row.** An `attempt` is exactly one runtime submission and the unit of cost. It records status, handle, lease, counters, `strategy` and `strategy_fallback`, and snapshots `price_table_id`, `image_digest` and `harness_version`. `artifact_lineage` rows link its `artifact_version` outputs to the versions the node instance consumed when it became ready (`consumed`, `reused_invariant`, `previous_attempt`). Earlier versions become `superseded`; scheduling never deletes them.

**Retry strategies** (FR-6.4): `fresh` (new workspace from the declared revision), `resume` (the previous attempt's session handle; needs the runtime capability `supports_resume`) and `fresh_with_feedback` (new workspace from the previous attempt's `VcsRef`, plus a `Feedback` DTO with the review comments, author and association). `fresh_with_feedback` is the documented default after reviewer rejection (requirements decision 2) because it works on every runtime; `resume` falls back to it (recording `strategy_fallback`) when the handle is gone or the installed harness version changed.

**Sub-graph iteration** (FR-2.7a, requirements decision 8). A sub-graph attempt is the integer `subgraph_attempt` on node instances, never an `attempt` row. A body node ends the iteration through a branch to `$fail_attempt`; the instance fails with class `attempt_failed`. Iteration N+1 re-materialises the body and receives `$previous_attempt`, a `PreviousAttempt` artifact `{subgraph_attempt, reason, error_class, artifacts}` stored as an ordinary artifact version; a node gets it only by declaring an input `from: $previous_attempt`. Nodes marked `attempt_invariant` are not re-run. The sub-graph's `max_attempts`, timeout and budget span all iterations.

**Side effects are never rolled back** (requirements decisions 3 and 9, FR-6.1b). kora never deletes or closes branches or change requests created by failed attempts; cleanup is the author's job, for example through a command node. Submission is at most once: the attempt row is committed before `runtime.submit`, the submission is keyed by the attempt id (runtimes dedupe on it) and `lookup(attempt_id)` recovers a handle after a crash.

**Clear downstream** (requirements decision 10, FR-6.8a). Only a manual retry and the start of sub-graph attempt N+1 invalidate downstream results, in one transaction under the run lock: the downstream closure through `consumed_versions` and `depends_on` (minus consumers listing the input in `independent_of`) returns to `pending` with its `current_attempt_id` cleared, its `current` versions become `superseded`, open waits are cancelled, pending approval requests become `invalidated`, non-terminal attempts get `desired_state = cancel`. Readiness is then an ordinary query. The same commit revokes the cancelled attempts' result authority: an upload or a `finishing` transition is accepted as current only from an attempt with `desired_state = run` that is still its instance's current attempt, so a write that lands between the commit and the attempt's next heartbeat is stored as historical; usage records and segments are accepted regardless, so no cost is lost. The lease epoch fences a previous lease holder, not the attempt's own late writes.

**Retention** (DD-4). A graph-level setting with a namespace default: run, node instance, attempt, event and usage records forever; session transcripts and segments 30 days after the run finished; artifact payloads 30 days (both lowered from 90 and 180 days on 2026-10-16); reference-only artifacts such as `VcsRef` forever. Only payloads are deleted; rows keep digest, lineage and `payload_state = expired`. Deletion is a separate low-priority batched task: candidates are marked `expiring`, a blob is deleted only when no other `present` or `expiring` version references its `storage_ref`, then rows become `expired`.

## Alternatives considered

- **Attempt as iteration too.** Rejected: one word, one meaning.
- **`resume` as the default after rejection.** Rejected: it depends on a runtime capability and a surviving handle.
- **Cleaning up branches of failed attempts.** Rejected: session side effects are not transactional, and failed attempts are evidence.
- **Generation counters and a dirty set.** Rejected for the transactional clear-downstream.
- **Retention inside the scheduling loop.** Rejected: it deleted shared blobs.

## Consequences

- Cost per accepted change, failed attempts and fan-out losers included, is derivable offline from `artifact_lineage` and `usage_record` (NFR-8.3).
- Authors must plan VCS cleanup explicitly.
- "Attempt" and "sub-graph attempt" stay distinct terms everywhere.

## Where it is applied

- [data-model.md §4](../../knowledge/specs/data-model.md) (state machines), [data-model.md §5](../../knowledge/specs/data-model.md) (attempts, sub-graph attempts, lineage), [data-model.md §6](../../knowledge/specs/data-model.md) (clear downstream), [data-model.md §10](../../knowledge/specs/data-model.md) (retention)
- [scheduler.md §3](../../knowledge/specs/scheduler.md) (at-most-once submission), [scheduler.md §7](../../knowledge/specs/scheduler.md) (retry handling), [scheduler.md §13](../../knowledge/specs/scheduler.md) (retention task)
- [ir.md §5](../../knowledge/specs/ir.md) (sub-graph attempt semantics), [ir.md §6](../../knowledge/specs/ir.md) (retry policy and strategy), [ir.md §2](../../knowledge/specs/ir.md) (`runtime.retention`)
- [protocols.md §2](../../knowledge/specs/protocols.md) (`submit`, `lookup`, `adopt`)
- [dsl.md §1](../../knowledge/specs/dsl.md) (`@subgraph(max_attempts=…)`, `fail_attempt`, `@graph(retention=…)`)
- [FR-2.7, FR-2.7a, FR-4.5, FR-4.6, FR-6.1a, FR-6.1b, FR-6.3, FR-6.4, FR-6.8, FR-6.8a](../requirements/functional.md); [functional.md §13, decisions 2, 3, 8, 9, 10](../requirements/functional.md)
- [NFR-2.1, NFR-2.4, NFR-8.3](../requirements/non-functional.md)
