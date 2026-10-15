# ADR-0012: No transform node kind; selection and bundling are agent nodes, kora attaches cost

- Status: accepted, 2026-10-15
- Deciders: project owner, during the design of the intermediate representation; the cost rule revised after the design review of 2026-10-15
- Absorbs: DD-8
- Related: ADR-0006, ADR-0008, ADR-0010

## Context

The canonical YQL optimisation graph has three steps between the agent sessions that do the real work: pack an accepted candidate (its `VcsRef`, metrics and hypothesis) into one `Candidate` artifact; pick the best candidate across the fan-out (largest `cpu_seconds` gain over baseline, at least 15 %, one per query); and assemble the `ReviewBundle` a human approves. None of these is open-ended work, and the temptation while designing the intermediate representation (IR) was to add a deterministic `transform` node kind, a small expression or template over typed inputs, plus a helper CLI for the review bundle, so that no model call would be spent on data shuffling. Such a node needs an expression language rich enough for list filtering, grouping and aggregation, a template language for prose and a cost-aggregation primitive: each a new surface to validate, document and keep in parity across the DSL, the IR and future SDKs and UI editors.

Separately, the approver must see what the run has cost so far. An early draft had the bundle step compute cost by walking lineage from the selected candidate; the design review of 2026-10-15 ([record](../../knowledge/reviews/review-2026-10-15.md)) flagged that lineage walk as a special case.

## Decision

There is no `transform` node kind and no helper CLI (DD-8). The IR keeps eight node kinds: `agent`, `command`, `human_input`, `wait_event`, `timer`, `branch`, `fan_out`, `subgraph`.

- **Packing, selection and bundling are ordinary agent nodes on a cheap model over typed inputs.** In the canonical example the nodes `accept`, `select_best` and `bundle` use the `packer` preset: harness `claude`, model `claude-haiku-5-5`, `max_events: 20`, `budget_usd: 0.5`, `sandbox: read_only`, a minimal tool policy (an empty allow list for `select_best`; `Read` and read-only git commands for `bundle`) and an `output_schema` the harness's structured output must satisfy. The prompt states the rule in words and the schema makes the result typed.
- **A command node serves authors who want a fixed formula.** `command` already runs argv in an isolated workspace and parses a declared `output_file` into a `StructuredOutput` or a custom artifact type (FR-2.8), so a script is the deterministic alternative without a new node kind.
- **kora attaches cost itself.** When the scheduler creates an approval request it sums the run's usage records at request time, per model, with the weakest completeness of its parts, into `approval_request.attempt_cost`; no node computes cost and no lineage walk runs. The approval view in the API and `kora web` show it next to the form.

This keeps the split that ADR-0008 fixes for the `ReviewBundle` ([0008-content-bound-approvals.md](0008-content-bound-approvals.md)): the agent `bundle` node writes only `narrative` (`summary`, `rationale`); kora writes `facts` from its own records (inputs with digests and provenance, gate results from `CommandResult` rows, metrics references, the `VcsRef` with its commit, attempt cost) and they are read-only to agents. Approvals bind to the digests of `facts`. The agent selects and explains; it never produces the evidence it is judged on.

## Alternatives considered

- **A `transform` node with its own expression language.** Rejected: a second computation model in the IR with its own validator, no clear boundary with the branch node's CEL, and a surface every SDK and UI editor would have to reproduce.
- **A helper CLI for the review bundle inside a command node.** Rejected: kora code running inside user workspaces with knowledge of kora's records; the facts now come from the control plane directly.
- **Cost by lineage walk from the selected candidate.** Rejected after the design review of 2026-10-15: the plain sum over the run is what the approver needs (every attempt, fan-out losers included) and has one definition. Cost per accepted change stays derivable offline from `artifact_lineage` and `usage_record` (NFR-8.3).

## Consequences

- Each packing or selection step costs a small model call, bounded by the preset's `max_events` and `budget_usd`, and produces an `AgentSession` artifact like any agent node; its usage records enter the run's cost.
- Selection rules live in prompts and output schemas; an author who needs bit-exact determinism writes a command node.
- The IR, the DSL and the validator stay small: one node vocabulary, CEL for routing, Jinja2 for prompts, nothing else.
- `attempt_cost` is summed at request time, a snapshot of the run's spend when the request was created; approval requests keep it regardless of retention.

## Where it is applied

- [ir.md §3](../../knowledge/specs/ir.md) (no node kind for building artifacts, `ReviewBundle` facts and narrative), [ir.md §4](../../knowledge/specs/ir.md) (node kinds table, command node outputs), [ir.md §4.2](../../knowledge/specs/ir.md) (cost attached to approval requests)
- [examples/yql-optimization.ir.yaml](../../knowledge/specs/examples/yql-optimization.ir.yaml) (`packer` preset; `accept`, `select_best`, `bundle` nodes)
- [data-model.md §5](../../knowledge/specs/data-model.md) (cost on an approval request) and `approval_request.attempt_cost` in [data-model.md §2](../../knowledge/specs/data-model.md)
- [scheduler.md §2](../../knowledge/specs/scheduler.md) (the admission pass sums usage records into `attempt_cost`)
- [api-and-agent-surface.md §2](../../knowledge/specs/api-and-agent-surface.md) (approvals route) and [api-and-agent-surface.md §6](../../knowledge/specs/api-and-agent-surface.md) (web frontend)
- [dsl.md §1](../../knowledge/specs/dsl.md) (`packer` registry preset)
- [FR-2.8, FR-4.2, FR-9.5](../requirements/functional.md); [functional.md §13, decision 17](../requirements/functional.md); [NFR-8.3](../requirements/non-functional.md)
