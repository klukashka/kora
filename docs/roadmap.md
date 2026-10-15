# Status and next steps

Status as of 2026-10-15. This page says where the project stands and what comes next; it is updated when a step completes. It records no decisions of its own: those are in [decisions/](decisions/README.md).

## Where the project stands

- Positioning, requirements and the design are written, reviewed in two cycles and consistent with each other; there are no open questions in the documents. Deferred work is tagged `[post-MVP]` in the requirements or written up in [proposals/](proposals/README.md).
- There is no code. Nothing in the design has been exercised against a real harness, runtime or database.
- The first implementation slice has its defaults recorded ([DD-36](../knowledge/reviews/design-decisions-2026-10-15.md)): `kora standalone` on SQLite, one agent node on the Claude Agent SDK and one command node on the local runtime, a manual trigger, a git `VcsRef`, approval through the CLI.

## What the documents cannot settle

These are the assumptions that only code can confirm, in the order they should be tested:

1. **Harness SDK behaviour.** The adapters ([ADR-0004](decisions/0004-harness-adapters-over-vendor-sdks.md)) and the trust boundary ([ADR-0009](decisions/0009-target-repository-trust-boundary.md)) rely on documented but untested behaviour: the Claude Agent SDK with `setting_sources=[]` and the `PreToolUse` hook, the Codex app-server protocol (`item/permissions/requestApproval`, `turn/completed.usage`), the Agent Client Protocol permission request. The first slice must drive a real session through each path it claims.
2. **The CEL binding.** The Rust-backed `common-expression-language` binding with kora's own schema checker ([ADR-0006](decisions/0006-graph-definition-dsl-and-yaml-representation.md)) has not been prototyped; the fallback is `cel-python` without static checking. Settle this in the first days of the first slice.
3. **The fake harness and the fake runtime.** The testing rules in [AGENTS.md](../AGENTS.md) forbid mocks, so almost every test depends on these two fakes (`NFR-10.1`) passing the same conformance suites as real implementations. Their contract is not written separately from the protocols; the first slice writes them before the real adapters.
4. **Scale.** The scheduler is designed for hundreds of concurrent attempts (`NFR-1`), which is unverified until a load test on PostgreSQL after the scheduler and worker exist as separate processes.
5. **Product hypotheses.** "External demand", "multi-harness value" and "outer-loop value" in [positioning.md](positioning.md) are tested by the pilot on the canonical YQL optimisation graph, not by code.

## Next steps, in order

1. **Implementation plan.** Vertical slices, each stating what it proves and how it is tested without model calls; the first slice is the one above, built around items 1 to 3. The plan is the next document; it goes into this folder.
2. **Repository scaffold** following the layout in [ADR-0013](decisions/0013-implementation-stack.md): `src/kora/`, `web/`, `tests/`, `conformance/`, CI under ten minutes on Linux and macOS.
3. **First slice**, then the acceptance that matters for positioning: the same graph running unchanged on a second runtime and VCS pair through the protocols (MVP acceptance in [requirements/functional.md](requirements/functional.md)).
4. **Pilot** on the YQL optimisation graph with the metrics listed under "Evidence, hypotheses and open risks" in positioning.

## How work is done here

- The project owner decides product features and architecture; proposals go to them as options with trade-offs and a recommendation, one question at a time.
- Decisions become records in `decisions/`, deferred changes become `proposals/`, and the specifications in `knowledge/specs/` are updated to match before code follows them.
- The rules in [AGENTS.md](../AGENTS.md) apply to every contributor, human or agent.
