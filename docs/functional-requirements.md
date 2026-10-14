# kora — Functional Requirements

Status: draft v0.3 (2026-10-15). Scope tags: **[MVP]** must ship in the first usable release; **[post-MVP]** designed for, not built. Decisions were taken by the project owner during requirements elicitation; precedents cited are from `reports/agentic-pipeline-orchestrators.md`.

MVP definition (owner's words): basic graphs can be run and deployed; all core functionality is present; no polished UI, no deep observability, no chat integrations (Telegram, Slack). Concretely, "basic graphs" means the canonical YQL graph in `positioning.md` runs end to end on the Docker runtime with both harnesses. The hypothesis "own scheduler is necessary" in `positioning.md` was tested by desk analysis (`docs/analysis/own-scheduler-vs-existing-engine/verdict.md`) before scope freeze.

## 1. Graph definition

- **FR-1.1 [MVP]** Graphs are defined in Python using decorators and the `>>` dependency operator in the style of Airflow TaskFlow (`@graph`, `@node`-style decorators; `a >> b >> [c, d]`). Passing a node's output as another node's argument also creates a dependency.
- **FR-1.2 [MVP]** Every graph compiles to a **YAML intermediate representation (IR)**. The scheduler consumes only the IR; it never imports user Python at run time. The IR carries a root `version` field and is validated against a published JSON Schema.
- **FR-1.3 [MVP]** The IR separates three concerns into distinct sections: (a) graph topology and node definitions, (b) graph-level runtime and policy configuration, (c) references to secrets (never secret values).
- **FR-1.4 [MVP]** `kora compile` produces the IR from Python; `kora validate` checks an IR against the schema and semantic rules (acyclicity, resolvable references, artifact type compatibility on edges). Compiled IR is a committed, machine-generated file that is never hand-edited (precedent: gh-aw `.lock.yml`, Windmill `flow.yaml`).
- **FR-1.5 [post-MVP]** Other language SDKs and a UI editor produce the same IR. Design rule: stable string node ids and expression references, not positional edges; lenient on unknown top-level keys, strict inside typed sub-objects.
- **FR-1.6 [MVP]** Dependency trigger rules per node, Airflow-style: `all_success` (default), `all_done`, `one_success`, `one_failed`, `none_failed`, `all_skipped`. When an upstream node is retried and succeeds, downstream nodes whose rules are now satisfied start automatically.
- **FR-1.7 [MVP]** Graph versioning: each run records the IR version/hash it ran with; re-runs of an existing run use the original IR unless explicitly told to use the latest.

## 2. Node kinds

- **FR-2.1 [MVP] Agent node.** Runs one full coding-agent session on a harness. Harness is a protocol; MVP implements **Claude Code** and **Codex**; OpenCode and Pi follow post-MVP.
- **FR-2.2 [MVP] Human-input node (approval gate).** Pauses the run and waits for a typed response through an approval channel (section 7). Has a form schema, a timeout, a default/expiry behaviour (`fail` | `skip` | `default_value`) and records approver identity. It holds no worker slot while waiting.
- **FR-2.3 [MVP] Wait-for-event / sensor node.** Blocks until an external condition holds: webhook received, CI status on a PR, PR merged, custom poll predicate. Has a poll interval and a timeout. Holds no worker slot while waiting.
- **FR-2.4 [MVP] Timer node.** Durable delay ("wait 24 h, then continue").
- **FR-2.5 [MVP] Branch node.** Deterministic routing on an upstream artifact's fields (e.g. `review.verdict == "approved"`). No LLM in the routing decision.
- **FR-2.6 [MVP] Fan-out / fan-in.** A list-typed artifact expands into N parallel instances of a node or sub-graph (`expand`-style mapping); a fan-in node collects their artifacts into a list artifact. Supports a parallelism cap and `skip_failures` semantics for the collection.
- **FR-2.7 [MVP] Sub-graph node.** A graph invoked as a node of another graph. Has its own retry policy, timeout and budget applied to the sub-graph as a unit; a sub-graph retry re-runs the whole sub-graph and marks its intermediate artifacts as superseded (they stay retained per FR-4.5). kora does **not** touch VCS side effects of failed attempts (branches, change requests stay as they are); cleaning them up is the graph author's responsibility, e.g. via a `CommandNode`. Sub-graphs are reusable across graphs and in fan-out.
- **FR-2.7a [MVP] Iteration semantics (the feedback loop).** The graph stays acyclic; a loop is expressed as a sub-graph retry. Rules:
  - An attempt ends in `success`, or in `failed(reason, artifacts)`; a node inside the sub-graph can end the attempt explicitly (`fail_attempt(reason=...)`, e.g. the review node when the verdict is "changes requested").
  - Attempt N+1 receives the sub-graph's original inputs plus a `previous_attempt` input: the failure reason and the artifacts of attempt N (for the YQL case: `VcsRef` of the candidate branch, the review verdict with comments, gate results). Nodes declare whether they consume `previous_attempt`.
  - The sub-graph's `max_attempts`, timeout and budget span all attempts; the per-node limits apply per attempt.
  - Everything inside the sub-graph re-runs; nothing is memoised across attempts unless a node declares its output as attempt-invariant (e.g. the baseline measurement), in which case it is reused.
  - Artifacts of all attempts are retained with attempt lineage (FR-4.5).
- **FR-2.8 [MVP] Command node.** Runs a shell command deterministically in an isolated workspace (same VCS checkout semantics as an agent node, same runtime protocol). Output artifact: exit code, captured stdout/stderr (truncated, full output stored as a reference), optional JSON parsed from a declared output file into a `StructuredOutput`. Intended for cheap mechanical gates outside the agent loop: build, tests, benchmarks, linters, VCS cleanup. Follows the pattern from every published case study (Dropbox: "not every step belongs inside the agent loop"). Not a general-purpose Python task runner: no user Python executed by the scheduler.

## 3. Node configuration and inheritance

- **FR-3.1 [MVP]** Agent-node configuration vocabulary: harness (`claude` | `codex`), harness version pin, model, system prompt (append), prompt template, skills, subagents, hooks, MCP servers, allowed/disallowed tools, permission mode, sandbox mode, workspace reference, output artifact schema, retry policy, timeouts, event-count limit, soft budget, runtime override, network policy field, `trust_repo_config` flag (FR-3.4), environment variables and secret references. The vocabulary is a superset modelled on Claude Code subagent frontmatter and mapped per harness.
- **FR-3.2 [MVP]** Layered inheritance with named presets: **global → graph → preset → node**. Each field has a documented merge rule (scalars override, lists such as skills/MCP servers append-and-dedupe, maps deep-merge), modelled on gh-aw `imports` and Airflow `client_defaults` precedence. The resolved per-node configuration is materialised in the IR so the scheduler never re-resolves it.
- **FR-3.3 [MVP]** Per-harness mapping: the harness adapter translates the resolved node config into the harness's native injection (Claude Code: `--bare -p` with `--settings`, `--agents`, `--mcp-config`, `--json-schema`, `--max-budget-usd`; Codex: `codex exec --json` with `-c` overrides, `--output-schema`, `--sandbox`, config profile). Fields that no harness can honour uniformly (turn limits, mid-session budget stops) are enforced by kora from the event stream rather than promised as harness features. Fields a harness cannot honour produce a validation warning at compile time.
- **FR-3.4 [MVP]** The target repository's own harness configuration (`CLAUDE.md`, `AGENTS.md`, `.claude/` settings, hooks, subagents, `.mcp.json`, `.codex/`) is **not** loaded by default: nodes run hermetically (`--bare` or the harness equivalent) and receive only what the resolved node config specifies. A node-config field `trust_repo_config` (default `false`, inheritable through the global → graph → preset → node layers) enables loading of the repository's harness configuration, including its instruction files. Rationale: hooks and MCP servers from a checked-out branch are executable code; the harness docs warn that non-hermetic headless runs execute them even in untrusted folders.

## 4. Artifacts

- **FR-4.1 [MVP]** Every edge carries a typed artifact. A base `Artifact` type defines: id, type name, JSON-Schema of the payload, producer (run, node, attempt), lineage (input artifact ids), created-at, storage reference.
- **FR-4.2 [MVP]** Built-in artifact types:
  - `StructuredOutput` — JSON validated against a Pydantic/JSON Schema declared on the node; produced via the harness's structured-output feature.
  - `VcsRef` — reference into the VCS: repository, branch, commit, optional change request (PR/review) URL and status, diff summary.
  - `AgentSession` — harness name and version, session id (resumable handle), transcript reference, token usage per model, cost estimate with cost basis and billing class, turn count, duration, tool-permission denials, stop reason.
  - `CommandResult` — exit code, captured output reference, optional parsed JSON (FR-2.8).
  - `ReviewBundle` — the evidence package for the single human touchpoint: the `VcsRef` under review (pinned to a commit), baseline vs candidate metrics (two `StructuredOutput`/`CommandResult` references and a diff), gate results, selection rationale (why this candidate among the fan-out), attempt history, and the aggregated cost of all attempts (computed by kora). The human-input node accepts a `ReviewBundle` as input; the VCS approval channel publishes it as a change-request comment.
- **FR-4.3 [MVP]** Users define custom artifact types by subclassing the base type with their own schema; custom types are usable on edges, in fan-out and in branch conditions.
- **FR-4.4 [MVP]** Artifact storage is a protocol; MVP stores artifact metadata in the metadata DB and payloads on local disk; object storage (S3-compatible) post-MVP. Large payloads are referenced by digest, not inlined.
- **FR-4.5 [MVP]** Artifacts are per attempt: a retried node produces a new artifact version; previous attempts' artifacts are retained for inspection (retry diffs).
- **FR-4.6 [MVP]** Node outputs (edge payloads) are distinct from node scratch state. Scratch state (e.g. a resumable session id used by a `resume` retry) survives retries; outputs do not.

## 5. Workspace and VCS

- **FR-5.1 [MVP]** Each agent node runs on an **isolated workspace**: a fresh checkout of the repository at a declared revision; the node's result is a branch/commit expressed as a `VcsRef` artifact. Nodes never share a working directory.
- **FR-5.2 [MVP]** VCS operations are a protocol (`checkout(repo, rev) → workspace`, `publish(workspace) → VcsRef`, `open_change_request(ref) → url`, `change_request_status(url)`). MVP implements **git** (clone/worktree, branch push, PR via GitHub/GitLab API). Enterprise VCS such as Yandex `arc` is implemented by the adopter and selected at graph level.
- **FR-5.3 [MVP]** The VCS implementation is chosen per graph (graph-level config), overridable per node.

## 6. Execution, scheduling and retries

- **FR-6.1 [MVP]** Control plane from day one: scheduler, metadata database, HTTP API, workers/runtime adapters. Run state lives only in the metadata DB; a scheduler restart resumes in-flight runs (adopting running sessions through the runtime protocol).
- **FR-6.1a [MVP] At-most-once submission.** Before calling the runtime, the scheduler persists the attempt with a kora-generated `submission_key`. `submit(workload, submission_key)` must be idempotent in every runtime (a second call with the same key returns the existing handle), and `lookup(submission_key)` recovers the handle after a crash between submit and handle persistence. Adoption after restart is by `submission_key`, not by handle.
- **FR-6.1b [MVP] Side-effect boundary.** kora guarantees at-most-once submission per attempt and records every VCS side effect it performs through the VCS protocol. Side effects made inside a session (pushes, change requests, MCP calls) are not transactional and are never rolled back; kora records what the harness reports (`VcsRef`, tool calls in the transcript) and exposes the attempt history so a human or a `CommandNode` can clean up. Retrying a node or sub-graph never deletes anything outside kora's own state.
- **FR-6.2 [MVP]** Triggers: manual (`kora trigger <graph> --param k=v`, API), cron schedule, webhook/event (HTTP endpoint with a secret, plus "triggered by another graph"), and sensor-driven start (poll predicate).
- **FR-6.3 [MVP]** Retry policy per node and per sub-graph: `max_attempts`, `initial_interval`, `backoff_coefficient`, `max_interval`, `retry_on` (error classes: harness error, timeout, budget exhausted, artifact validation failed, custom predicate on the artifact), `non_retryable` classes.
- **FR-6.4 [MVP]** Agent-node retry **strategy** is chosen by the graph author per node:
  - `fresh` — new session, clean workspace, same inputs;
  - `resume` — resume the same harness session with a feedback message (uses the `AgentSession` handle);
  - `fresh_with_feedback` — new session that receives the previous attempt's artifacts and failure reason as additional input.

  The documented default pattern for "reviewer rejected with comments" is `fresh_with_feedback`: a new session on a workspace checked out from the attempt's branch, with the `VcsRef` and the review comments as inputs. `resume` is documented as an optimisation for runtimes that can keep or snapshot the session's environment (local process, pause-capable sandboxes); a `resume` whose session is no longer available falls back to `fresh_with_feedback` and records that on the attempt.
- **FR-6.5 [MVP]** Timeouts: per node wall-clock, per run wall-clock, per human-input/sensor wait. Expiry behaviour is configurable per node.
- **FR-6.6 [MVP]** Event-count limit per agent node (`max_events`: turns and tool calls counted by kora from the harness's JSONL stream) with a classified error (`events_exhausted`) usable in `retry_on`. kora enforces it itself by terminating the session; harness-native turn limits are used in addition where they exist (Agent SDK `maxTurns`; no `--max-turns` in Claude Code CLI 2.1.299 or `codex exec` 0.162.1). Codex's experimental `features.rollout_budget.limit_tokens` is mapped from the soft budget when enabled.
- **FR-6.7 [MVP]** Cancellation of a run or a node with a grace period; runtime adapters must terminate the session and release the workspace.
- **FR-6.8 [MVP]** Manual re-run of a failed node (and everything downstream via trigger rules) from the CLI/API without restarting the run.
- **FR-6.8a [MVP] Artifact versions and downstream invalidation.** Node results are bound to the versions of the artifacts they consumed. When an upstream node is retried or re-run and produces a new artifact version, every downstream node that consumed the old version is reset to pending and re-runs when its trigger rule is satisfied again (Airflow "clear downstream" semantics, automatic); its earlier results are kept as superseded. Pending approvals and sensor waits on superseded versions are cancelled; fan-in recollects. A downstream node may declare its result as independent of a given input to opt out.
- **FR-6.9 [post-MVP]** Per-graph quotas per period (e.g. max estimated spend per week) that pause scheduling when exceeded.
- **FR-6.10 [MVP]** Concurrency controls: global, per-graph and per-runtime parallelism limits; fan-out parallelism cap.

## 7. Approvals (human input)

- **FR-7.1 [MVP]** The approval channel is a protocol: `request(form_schema, context) → request_id`, `poll/notify(request_id) → response | pending`, `cancel(request_id)`. Responses are typed data validated against the form schema; approver identity and timestamp are recorded.
- **FR-7.2 [MVP]** Built-in channels: **CLI/API** (`kora approve <run> <node> --input ...`, REST endpoint) and **VCS change request** (approval = PR approved/merged, rejection = PR closed or "changes requested"; review comments are captured as the response payload so the next attempt can receive them, by default through `fresh_with_feedback`, optionally through `resume`; see FR-6.4).
- **FR-7.2a [MVP] Approval is bound to content, not to a URL.** An approval request records the exact commit SHA and the digests of the gate results it covers. A VCS approval is accepted only if the change request's head SHA equals the recorded one; a new push invalidates the pending request and kora re-requests (or re-runs gates first, per graph config). The approval record stores approver, timestamp, SHA and digests.
- **FR-7.3 [MVP]** A **minimal web page** listing pending approvals with a form and a button, functional, not designed.
- **FR-7.4 [post-MVP]** Chat channels (Slack, Telegram) and enterprise approval systems implemented against the same protocol.
- **FR-7.5 [MVP]** Gate state is stored as data (no serialised callables), following the Airflow CVE-2026-67260 lesson.

## 8. Runtimes

- **FR-8.1 [MVP]** Runtime is a protocol with the verb union of Airflow executors, Prefect workers, Dagster launchers and Argo plugins: `submit(workload) → handle`, `status(handle)`, `logs(handle)`, `cancel(handle, grace)`, `adopt(handle)` after controller restart, `heartbeat(handle)`, plus declarative capability flags (isolation class, supports pause, supports snapshot, supports network policy). The workload DTO carries the resolved node config, workspace reference, secret references and artifact input references, never user code.
- **FR-8.2 [MVP]** Built-in runtimes: **local process** (harness subprocess on the host) and **Docker** (one container per session, repository checked out inside, harness and credentials injected).
- **FR-8.3 [post-MVP]** Cloud sandbox runtime (one of E2B / Daytona / Modal class, with pause/snapshot) as the second wave; **Kubernetes** (one Job/Pod or `agent-sandbox` per session) as the third wave or community-contributed.
- **FR-8.4 [MVP]** Runtime is selected at graph level and overridable per node (precedent: Airflow `executor=` per task).
- **FR-8.5 [MVP]** Secrets reach the runtime only through the secrets protocol (section 10); the runtime adapter decides the injection method (env var, file, proxy).
- **FR-8.6 [MVP]** Network policy is a field of the node config (egress allowlist); runtimes that cannot enforce it report that through capability flags and compile-time warnings.
- **FR-8.7** Adopters implement enterprise runtimes (e.g. Yandex Arcadia CI / Sandbox / Tasklets) against the same protocol; kora ships a conformance test suite for runtime implementations.

## 9. Cost accounting and limits

- **FR-9.1 [MVP]** Unit of record is **tokens per model per node attempt** (input, cached input, output; cache creation where reported), read from the harness result (Claude Code `usage`/`modelUsage`, Codex `turn.completed.usage`). Each usage record carries a completeness state: `complete` (final result received), `partial` (summed from streamed per-turn events after a crash or kill; Codex reports usage only at `turn.completed`, so a killed turn is unaccounted), `unavailable`. For `resume` retries, where the harness reports whole-session totals, the attempt's usage is the delta between the totals after and before the resume.
- **FR-9.2 [MVP]** A **pluggable price table** converts tokens to a USD-equivalent estimate; kora ships a default list-price table and allows overriding it (corporate contract, gateway prices). When the harness reports its own cost with a trustworthy basis (Claude Code `costBasis` in `list` | `managed`), that figure is used.
- **FR-9.3 [MVP]** Each `AgentSession` artifact records a **billing class** (`api_key` | `subscription` | `gateway` | `managed`) so estimates can be interpreted correctly; subscription-based runs are labelled "not billable in USD".
- **FR-9.4 [MVP]** **Hard limits** enforced by kora: node and run timeouts, `max_events` (FR-6.6). **Soft limit**: USD-equivalent budget per node and per run; for Claude Code it is also propagated to `--max-budget-usd`, for Codex kora computes spend from the token stream and terminates the session when exceeded. All USD figures are labelled as estimates.
- **FR-9.5 [MVP]** Run and node views show tokens, estimated cost and cost per attempt; fan-out views aggregate across instances.
- **FR-9.6 [post-MVP]** Reconciliation against vendor Admin usage/cost APIs and gateway spend; period quotas (FR-6.9).

## 10. Secrets

- **FR-10.1 [MVP]** Secrets provider is a protocol: `resolve(reference) → value` with references like `secret://<provider>/<name>`. MVP implements environment variables and a local file; Vault, Kubernetes Secrets and enterprise providers post-MVP or by adopters.
- **FR-10.2 [MVP]** Secret values never appear in the IR, artifacts, logs or transcripts; kora redacts known secret values from captured output.

## 11. Observability

- **FR-11.1 [MVP]** Run and node status, timeline and failure reason via CLI (`kora runs`, `kora run <id>`, `kora logs <run> <node> --follow`) and HTTP API.
- **FR-11.2 [MVP]** Session event stream (harness JSONL) is captured per attempt and stored as the transcript reference of the `AgentSession` artifact; transcripts are retained per attempt.
- **FR-11.3 [MVP]** Observability exporter is a protocol (`on_run_*`, `on_node_*`, `on_session_event`, `on_artifact`) with **opt-in** implementations: **OpenTelemetry** (GenAI semantic conventions: `invoke_agent` span per node, inference and tool spans, `gen_ai.usage.*` attributes, run/session ids as join keys), **Langfuse**, **Datadog LLM Observability**, and a documented path for custom exporters (e.g. Yandex Monium LLM Traces).
- **FR-11.4 [MVP]** Audit log: approval decisions with approver identity, tool-permission denials from the harness, run → node → attempt → session → VcsRef linkage.
- **FR-11.5 [post-MVP]** Web UI for graph visualisation and run inspection; retry diff viewer.

## 12. Control plane, CLI and deployment

- **FR-12.1 [MVP]** Components: scheduler, metadata DB (PostgreSQL; SQLite for standalone/dev), HTTP API, runtime workers/adapters, minimal web page for approvals.
- **FR-12.2 [MVP]** CLI: `kora compile`, `kora validate`, `kora deploy <ir>` (register a graph), `kora trigger`, `kora runs`, `kora run`, `kora logs`, `kora approve`, `kora retry <run> <node>`, `kora cancel`, `kora standalone` (single process with SQLite for development).
- **FR-12.3 [MVP]** Deployment: `docker-compose` (Postgres + scheduler + API + local/Docker runtime) and `kora standalone`. Helm chart for the control plane post-MVP, together with the Kubernetes runtime.
- **FR-12.4 [MVP]** Namespaces/projects group graphs, secrets references, presets and quotas. No RBAC in MVP: a single API token; authentication/authorisation is a protocol for later.
- **FR-12.5 [MVP]** Graph registration is versioned: deploying a new IR creates a new graph version; running runs keep theirs.

## 13. Resolved questions (decision log)

Decided by the owner on 2026-10-15:

1. **Deterministic gate nodes** → `CommandNode` is in MVP (FR-2.8). Mechanical gates run outside the agent loop.
2. **Reviewer rejected with comments** → both `resume` and `fresh_with_feedback` are supported; the documented default is `fresh_with_feedback` (FR-6.4, FR-7.2).
3. **Sub-graph retry and VCS side effects** → kora never deletes or closes branches/change requests created by failed attempts; artifacts are retained and marked superseded (FR-2.7, FR-4.5).
4. **Target repository's harness config** → not loaded by default; `trust_repo_config` flag on the node config, default `false`, covers instruction files too (FR-3.4).
5. **Tagline** → "Orchestrate coding-agent sessions as a graph" (see `positioning.md`).

Decided on 2026-10-15 after external review (round 2):

6. **MVP scope** stays as defined; `positioning.md` gains an explicit evidence / hypotheses / risks section, including the hypothesis "own scheduler is necessary" (own scheduler vs existing engine + adapter) with a test to run before scope freeze.
7. **Review bundle** → built-in `ReviewBundle` artifact in MVP, consumed by the human-input node and published to the change request by the VCS approval channel (FR-4.2, FR-7.2a). Rationale: the owner's model has one human touchpoint per target, the final PR; it must be evidence-backed.
8. **Feedback loop** → formal iteration semantics for sub-graph retry (FR-2.7a).
9. **Reliability** → at-most-once submission via `submission_key`, explicit side-effect boundary (FR-6.1a, FR-6.1b).
10. **Approvals and versions** → approvals bound to commit SHA and gate digests; automatic downstream invalidation on upstream retry (FR-7.2a, FR-6.8a).
11. **Harness limits** → `max_turns` replaced by kora-enforced `max_events`; usage records carry `complete/partial/unavailable` (FR-6.6, FR-9.1).

12. **Own scheduler vs existing engine + adapter** → desk analysis of Temporal, Kestra and Airflow (`docs/analysis/own-scheduler-vs-existing-engine/verdict.md`): own control plane for MVP; run state is interpretable data with explicit attempt objects and waits as rows so a Temporal-backed execution backend stays possible; Airflow and Kestra are integration targets, not cores.

13. **Primary contrast** → Archon (open-source YAML workflow engine for coding-agent sessions) replaces vendor cloud sessions as the primary contrast in `positioning.md`; cloud sessions become a secondary contrast.

Remaining open items are tracked as **[post-MVP]** tags, not as open questions.

## Appendix A — Illustrative DSL sketch (not a spec)

```python
from kora import graph, node, approval, expand, branch, subgraph, presets

optimizer = presets.claude(model="opus", skills=["yql-optimizer"], max_events=200, budget_usd=15)
reviewer  = presets.codex(model="gpt-5-codex", skills=["yql-review"], max_events=100)

@subgraph(max_attempts=3, budget_usd=60, timeout="6h")
def attempt(hypothesis: Hypothesis, baseline: MetricsReport, previous_attempt: Attempt | None) -> Candidate:
    change  = node.agent("implement", preset=optimizer, output=VcsRef)(hypothesis, previous_attempt)
    run     = node.command("dev_run", cmd="etl dev-run --json=out.json", output_file="out.json",
                           output=MetricsReport)(change)
    same    = node.command("equivalence", cmd="etl diff-results --baseline=$BASELINE", output=CommandResult)(run, baseline)
    verdict = node.agent("review", preset=reviewer, output=ReviewVerdict)(change, run, baseline, same)
    return branch(verdict.approved and same.exit_code == 0,
                  then=Candidate(change, run),
                  else_=fail_attempt(reason=verdict.comments))

@graph(schedule="0 6 * * 1", runtime="docker", vcs="git", trust_repo_config=False)
def yql_optimization():
    target   = node.agent("find_target", preset=optimizer, output=Target)()
    baseline = node.command("baseline", cmd="etl dev-run --json=out.json", output_file="out.json",
                            output=MetricsReport)(target)
    hyps     = node.agent("hypotheses", preset=optimizer, output=list[Hypothesis])(target, baseline)
    cands    = expand(attempt, over=hyps, baseline=baseline, max_parallel=3)
    best     = select_best(cands, metric="cpu_seconds", min_gain=0.15)   # fan-in + deterministic choice
    bundle   = review_bundle(best, baseline=baseline, attempts=cands)
    ok       = approval("final_pr", channel="vcs", form=HumanVerdict, timeout="3d")(bundle)
    node.wait_for("prestable_rollout", event="deploy.finished", timeout="2d")(ok)
```
