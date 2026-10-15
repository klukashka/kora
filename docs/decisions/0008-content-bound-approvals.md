# ADR-0008: Approvals bind to content — commit SHA plus ReviewBundle fact digests — and VCS approvals must come from a real reviewer

- Status: accepted, 2026-10-15
- Deciders: project owner, after the design review of 2026-10-15 (second cycle, threat-model lens), see [review-2026-10-15.md](../../knowledge/reviews/review-2026-10-15.md)
- Absorbs: requirements decision 7, requirements decision 10 (the approval part; downstream invalidation is [ADR-0007](0007-attempt-model-and-retries.md)), DD-33, DD-35
- Related: [ADR-0005](0005-eleven-protocols-with-conformance-suites.md), [ADR-0007](0007-attempt-model-and-retries.md), [ADR-0009](0009-target-repository-trust-boundary.md), [ADR-0010](0010-cost-accounting-and-limits.md), [ADR-0011](0011-namespaces-and-personal-tokens.md), [ADR-0012](0012-no-transform-node.md)

## Context

In the owner's model of an agentic pipeline there is one human touchpoint per target: the final change request. Everything before it happens without a person, so that touchpoint must be evidence-backed (requirements decision 7). The human-input node (FR-2.2) pauses the run and waits for a typed response through an approval channel; the built-in channels are the CLI/API and the change request on the version control system (VCS), with a web view in `kora web` (FR-7.2, FR-7.3).

The threat-model lens of the design review of 2026-10-15 (second cycle) raised two findings: an agent could write the evidence it is then judged on, and a change-request author, kora's own bot, or a plain merge could count as the approval. Together with requirements decision 10 (an approval must not silently cover a branch that moved after the reviewer looked), they shaped the decision.

## Decision

- **Binding to content, not to a URL.** An approval request records the exact commit SHA of the `VcsRef` under review and the digests of the `ReviewBundle.facts` it covers (`binds.vcs_ref` and `binds.gate_digests` in the node config; `bound_commit_sha` and `bound_digests` on the `approval_request` row). A response whose observed SHA or digests differ is refused with `API006` and the request stays `pending`; a VCS channel that observes a new push moves the request to `invalidated`, and the node re-requests with the new SHA. Which fields of an artifact enter a digest is declared by its artifact type (`digest_fields`).
- **`ReviewBundle` is split into facts and narrative** (DD-33). `facts` are written by kora from its own records and are read-only to agents: every input artifact of the bundle node with type, digest and producing attempt; gate results from `CommandResult` rows with exit codes; metrics references; the diff and commit references of the `VcsRef`; the run's attempt cost; and provenance per item (attempt, worker, image digest). The agent node writes only `narrative`: `summary` and `rationale`. The web view and the change-request comment render the two parts visibly apart.
- **Approval channels are a protocol** (`request`, `poll`, `feedback`, `notify`, `cancel`) with capability flags `push_notifications`, `carries_identity` and `observes_head_sha`. A channel returns what it observed (payload, approver login and permission level, `is_author`, `is_bot`, timestamp, head SHA and digests) and never decides validity; the scheduler compares SHA and digests and applies the rules below.
- **VCS approval rules** (DD-35). An approval from the change-request channel counts only when the approver is not the change-request author, not kora's own bot identity and, when the namespace policy sets `approver_allowlist`, on that list. "Merged" is a separate signal, accepted only when the channel configuration sets `accept_merged_as_approval` (default `false`). Approver login and permission level are recorded. Feedback comments fed into a `fresh_with_feedback` retry carry author and association, filtered to collaborators with write access by default (`feedback_from: collaborators_write`).
- **CLI/API approvals** require a personal token with the `approve` flag; the recorded approver is the token owner ([ADR-0011](0011-namespaces-and-personal-tokens.md)).
- **Cost next to the form.** kora itself attaches the run's aggregated estimated cost to every request; no agent computes it ([ADR-0010](0010-cost-accounting-and-limits.md)).

## Alternatives considered

- **Approving a node by name, without binding.** The approver would be saying yes to whatever the branch holds now; a push between review and response, or an upstream retry that regenerated the bundle, would pass unnoticed. Rejected: it breaks the single-touchpoint model.
- **Letting the agent author the whole bundle.** What the first design did, and simpler, but the agent under review would also author the gate results it is judged on. Rejected after the threat-model finding; facts now come from kora's own `CommandResult`, `artifact_version` and `usage_record` rows.
- **Treating a merge as an approval by default.** A merge can be performed by the author or by automation. Kept only as an explicit opt-in per channel configuration.

## Consequences

- The approval record is an audit entry: approver identity, channel, permission level, timestamp, SHA and digests (FR-11.4); `run_cost` and the bound digests survive payload retention.
- Downstream invalidation ([ADR-0007](0007-attempt-model-and-retries.md)) moves pending requests to `invalidated` and notifies the channel, so an upstream retry can never be approved against stale evidence.
- The approval-channel conformance suite checks the round trip, a stale response reported with its observed SHA, and idempotent cancel and notify; chat and enterprise channels plug into the same protocol later (FR-7.4).
- `kora validate` enforces that `binds` paths resolve to a `VcsRef` and to digest-bearing artifacts (`IR005`).
- Platform teams gain settings per namespace and per graph: `approver_allowlist`, `accept_merged_as_approval`, `feedback_from`.

## Where it is applied

- [FR-2.2](../requirements/functional.md), [FR-4.2](../requirements/functional.md) (`ReviewBundle`), [FR-7.1, FR-7.2, FR-7.2a, FR-7.3](../requirements/functional.md), [FR-11.4](../requirements/functional.md)
- [NFR-4.6](../requirements/non-functional.md)
- [ir.md §2](../../knowledge/specs/ir.md) (`runtime.approval_channels`), [ir.md §3](../../knowledge/specs/ir.md) (`ReviewBundle` facts and narrative), [ir.md §4.2](../../knowledge/specs/ir.md) (human-input node, `binds`, VCS rules), [ir.md §10](../../knowledge/specs/ir.md) (`IR005`)
- [data-model.md §2](../../knowledge/specs/data-model.md) (`approval_request`, `namespace.policy.approver_allowlist`), [data-model.md §4](../../knowledge/specs/data-model.md) (`approval_request` state machine, `API006`), [data-model.md §5](../../knowledge/specs/data-model.md) (cost on an approval request)
- [protocols.md §5](../../knowledge/specs/protocols.md) (approval channel), [protocols.md §8](../../knowledge/specs/protocols.md) (`digest_fields`), [protocols.md §13](../../knowledge/specs/protocols.md) (conformance)
- [design-decisions-2026-10-15.md](../../knowledge/reviews/design-decisions-2026-10-15.md) DD-8, DD-33, DD-35
