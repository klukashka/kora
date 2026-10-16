# kora knowledge base

Reference material for engineers and coding agents working on kora. Nothing here is read cover to cover; each file is loaded when a task needs it. Files here may be produced by agents and are not necessarily reviewed line by line by a person; where anything here disagrees with `docs/`, which the project owner has reviewed in full, `docs/` wins and this folder is fixed. The human-facing documentation (positioning, requirements, architecture overview, decision records, proposals) is in [`docs/`](../docs/README.md) and is the place where decisions are made; the files here apply them.

## Which file to load for which task

| task | load |
|---|---|
| Implement or change the graph document format, its schema or the compiler | [specs/ir.md](specs/ir.md), [specs/ir.schema.json](specs/ir.schema.json), [specs/examples/](specs/examples/), [specs/dsl.md](specs/dsl.md) |
| Write or change Python graph code, presets, the compile step | [specs/dsl.md](specs/dsl.md), then [specs/ir.md](specs/ir.md) for what it compiles to |
| Add or change a table, a state machine, retention, lineage | [specs/data-model.md](specs/data-model.md) |
| Scheduling passes, leases, waits, timeouts, retries, concurrency, standalone mode, migrations, the operability runbook | [specs/scheduler.md](specs/scheduler.md) |
| Implement a provider (runtime, harness adapter, VCS, approval channel, secrets, artifact storage, artifact type, observability exporter, authentication, sensor, policy) or its conformance suite; the harness mapping tables | [specs/protocols.md](specs/protocols.md) |
| Any HTTP endpoint, CLI command, MCP tool, harness skill or web console view, and the parity rules between them | [specs/api-and-agent-surface.md](specs/api-and-agent-surface.md) |
| Component-level diagrams of the scheduler and the worker, the attempt sequence, deployment shapes, the full stack table with alternatives, the glossary of canonical terms | [architecture/detailed.md](architecture/detailed.md) |
| Why a decision was taken, what was rejected | [docs/decisions/](../docs/decisions/README.md) first; the flat register of design decisions with "where applied" pointers is [reviews/design-decisions-2026-10-15.md](reviews/design-decisions-2026-10-15.md) |
| What the design reviews found and how each finding was dispositioned | [reviews/review-2026-10-15.md](reviews/review-2026-10-15.md) |
| The architecture critique before implementation (execution authority, evidence, trust boundaries, versioning, cost, competitors, engine choice) with the owner's disposition and the issue for each finding | [reviews/architecture-critique-2026-10-16.md](reviews/architecture-critique-2026-10-16.md) |
| Whether to build on Temporal, Kestra, Airflow or DBOS instead of an own scheduler, requirement by requirement | [analysis/own-scheduler-vs-existing-engine/verdict.md](analysis/own-scheduler-vs-existing-engine/verdict.md) and the per-engine files beside it |
| Competitors and adjacent systems (Archon, vendor cloud sessions, agent-graph frameworks, classic orchestrators, runtimes and sandboxes, cost accounting options, declarative specifications, durable execution and observability) | [research/agentic-pipeline-orchestrators.md](research/agentic-pipeline-orchestrators.md) (report), then the notes in [research/agentic-pipeline-orchestrators/](research/agentic-pipeline-orchestrators/) |
| Archon compared with kora aspect by aspect, with the Archon file and the kora requirement or decision behind every row | [research/agentic-pipeline-orchestrators/archon-vs-kora.md](research/agentic-pipeline-orchestrators/archon-vs-kora.md) |
| Through which mechanism each harness lets the session runner intercept and deny a tool call, with the vendor sources and the date checked | [research/harness-policy-hooks.md](research/harness-policy-hooks.md) |

## Folders

- `specs/`: normative specifications of the MVP design. They cite requirements (`FR-n.m`, `NFR-n.m`) and decisions (`ADR-000n`, `DD-n`) and are the source for the implementation; when a specification and a decision record disagree, the decision record wins.
- `architecture/`: the detailed diagrams and tables behind [docs/architecture.md](../docs/architecture.md).
- `reviews/`: dated review records and the flat register of design decisions (`DD-n`) that the specifications cite. The register is historical; its decisions are grouped and explained in `docs/decisions/`.
- `analysis/`: desk analyses that answered a specific question; each states its method and date.
- `research/`: competitor and market research with sources; dated, not maintained after the fact.
