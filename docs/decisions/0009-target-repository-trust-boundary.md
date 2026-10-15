# ADR-0009: The target repository is untrusted by default — `trust_repo_config`, protected paths and tool policy

- Status: accepted, 2026-10-15
- Deciders: project owner, during requirements elicitation (requirements decision 4), after the design review of 2026-10-15 (first cycle: DD-29) and after its second cycle (DD-34)
- Absorbs: requirements decision 4; DD-34; the tool-policy part of DD-29
- Related: [ADR-0004](0004-harness-adapters-over-vendor-sdks.md), [ADR-0005](0005-eleven-protocols-with-conformance-suites.md), [ADR-0008](0008-content-bound-approvals.md), [ADR-0011](0011-namespaces-and-personal-tokens.md)

## Context

An agent node checks out a repository and lets a coding agent work in it. Left to themselves, Claude Code and Codex load the folder's own `CLAUDE.md`, `AGENTS.md`, `.claude/` settings, hooks, subagents, `.mcp.json` and `.codex/`. Hooks and MCP servers are executable code, and the harness documentation warns that non-hermetic headless runs execute them even in untrusted folders (FR-3.4); on a shared platform the branch may have been written by anyone, including a previous agent attempt. Three threats shaped the decision, the last two identified by the threat model of the [design review of 2026-10-15](../../knowledge/reviews/review-2026-10-15.md) ([protected gate paths](../../knowledge/reviews/review-2026-10-15.md#protected-gate-paths) and [tool deny coverage](../../knowledge/reviews/review-2026-10-15.md#tool-deny-coverage)):

1. **Prompt injection through repository files.** Instruction files on a branch redirect the agent; hook and MCP definitions run the branch author's code with the session's credentials.
2. **An agent editing its own gate.** Command nodes run tests and benchmarks from the same checkout; an agent that edits `Makefile` or `tests/` passes the gate without doing the work.
3. **A deny that does not reach every tool call.** Harness allowlists differ per permission mode and adapter; shell chaining or `bash -c` hides a denied command inside an allowed one.

## Decision

**Hermetic by default.** `trust_repo_config` defaults to `false` and is inherited global → graph → preset → node. With `false`, the `claude` adapter runs with `setting_sources=[]` and a per-attempt config directory, the `codex` adapter marks the project untrusted and sets `project_doc_max_bytes=0`, and the `acp` adapter applies a hermetic workspace overlay, warning when the agent reads project config regardless. `true` requires the namespace policy `allow_trust_repo_config`, else `IR017`; a registry preset that raises it above the namespace policy fails compilation with `DSL013`. The resolved flag, permission mode and tool policy are auditable parts of the resolved config (NFR-4.2).

**Protected paths.** A graph declares `runtime.protected_paths` and a node adds `protected_paths`: gate scripts, build files, test directories, tooling configuration. The tool policy denies `Edit` and `Write` on them for every agent node of the graph, and before a command gate runs the worker calls `Vcs.diff_paths(workspace, base_commit, paths)` on the candidate checkout and fails the attempt with class `protected_path_modified` on any change. There is no second checkout.

**One `tool_policy`.** A node carries exactly one tool-permission field, `tool_policy` with `allow` and `deny` patterns; the DSL's `allowed_tools` and `disallowed_tools` compile into it. The namespace's `tool_policy` is prepended, so a namespace deny always wins; `IR013` validates the patterns. The MVP implementation `allow_deny` rejects, at argv level, shell chaining metacharacters and `-c` or `--exec` style indirection inside an allow-listed command. A denial travels as a `permission_denied` session event on the `AgentSession` artifact and becomes an event row for the audit log (FR-11.4).

**The policy protocol is the decision point.** The session runner invokes it before every tool call through the harness's own hook: Claude Agent SDK `PreToolUse`, Codex `approval_policy = on-request` with `item/permissions/requestApproval`, ACP `session/request_permission`. The decision is local to the session; the agent never reaches kora. The policy conformance suite requires that a deny reaches every tool call under every permission mode and adapter, never reaches the tool, and that a protected path cannot be edited; the harness conformance suite includes a repository hook that must not run.

## Alternatives considered

- **Trusting repository configuration by default, as the harnesses do.** Rejected (requirements decision 4): on a shared platform the repository is the agent's output, not the platform's configuration; instruction files fall under the same flag because they steer the agent as much as hooks do.
- **Relying on the harness sandbox alone.** Rejected: the `sandbox` field maps differently per harness and only warns on ACP, does not see MCP tool calls and does not know which paths are gates; it stays as a second layer.
- **Harness-native allowlists only.** Rejected by the [design review of 2026-10-15](../../knowledge/reviews/review-2026-10-15.md#central-tool-call-policy): no central decision point, no audit trail.
- **A second clean checkout for gates.** Rejected by DD-34: digest verification against the base commit is sufficient and cheaper.
- **Separate `allowed_tools`, `disallowed_tools` and `policy` fields in the IR.** Collapsed by the [same review](../../knowledge/reviews/review-2026-10-15.md#one-tool-policy-field) into one field.

## Consequences

- Agents do not see the repository's `CLAUDE.md` or `AGENTS.md` unless the node is trusted; authors put project instructions into the node configuration or obtain the namespace permission.
- Gate files must be declared; an undeclared gate file is editable by the agent.
- Digest verification runs at gate time, so an edit that slipped past the tool policy costs the attempt; hermetic behaviour is best-effort on ACP agents that read project configuration regardless.
- Cedar and OPA evaluators can replace `allow_deny` as plugins without a change to the intermediate representation.

## Where it is applied

- [FR-2.8, FR-3.1, FR-3.4, FR-8b.1, FR-11.4](../requirements/functional.md)
- [NFR-4.2, NFR-4.6](../requirements/non-functional.md)
- [protocols.md §3 (conformance: hermetic behaviour), §4 (`diff_paths`), §12, §13, §14](../../knowledge/specs/protocols.md)
- [ir.md §2 (`runtime.protected_paths`), §4.1, §6 (`protected_path_modified`), §10 (`IR013`, `IR017`)](../../knowledge/specs/ir.md)
- [dsl.md §1, §2 (`DSL013`), §3, §5](../../knowledge/specs/dsl.md)
- [data-model.md `namespace` (`tool_policy`, `allow_trust_repo_config`)](../../knowledge/specs/data-model.md)
- [detailed.md §7 (policy), §8](../../knowledge/architecture/detailed.md)
- [DD-29, DD-34](../../knowledge/reviews/design-decisions-2026-10-15.md)
