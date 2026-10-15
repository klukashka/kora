# Archon (coleam00) vs kora — October 2026

Researched 2026-10-15 from primary sources (GitHub repo, releases, CHANGELOG, issues, archon.diy docs) plus two secondary sources (a review and a livestream transcript). Headline correction to the brief: **the "command center for AI coding assistants" (Python/FastAPI/Supabase/MCP server) is the archived v1; since April 2026 Archon is a TypeScript/Bun YAML workflow engine for coding agents — i.e. a direct neighbour of kora, not a context server.**

## Q1. What Archon is today (purpose, maturity, stack, deployment)

### Takeaway
Archon is "the first open-source harness builder for AI coding": a YAML DAG engine whose nodes are Claude Agent SDK / Codex SDK / Pi sessions mixed with bash/script nodes, each run in its own git worktree. MIT, 23.7k stars, 11 releases since April 2026, latest v0.11.1 (2026-09-25), active (pushed 2026-10-14), ~200 open issues, breaking changes in nearly every minor.

### Cited Findings
- Repo description: "The first open-source harness builder for AI coding. Make AI coding deterministic and repeatable." Stars 23,689, forks 3,506, open issues 223 (API count), license MIT, language TypeScript, default branch `dev`, created 2025-02-07, pushed 2026-10-14, archived: false, homepage archon.diy, topics include `workflow-engine`, `bun`, `yaml`, `cli` — [GitHub API](https://api.github.com/repos/coleam00/Archon)
- README: "a workflow engine for AI coding agents"; "Same workflow, same sequence, every time"; each run gets its own git worktree; "Composable: mixes bash, test, and git nodes with AI nodes"; workflows live in `.archon/workflows/` and run from CLI, Web UI, Slack, Telegram or GitHub; architecture = platform adapters → Orchestrator → Command Handler / Workflow Executor / AI Assistant Clients; storage "SQLite / PostgreSQL (14 core tables)"; 3,644 commits — [README](https://github.com/coleam00/Archon)
- Docs tagline: "Package your AI coding workflows as YAML"; "Multi-provider: Supports the Claude Code SDK, Codex SDK, and local models via Pi" — [archon.diy/docs](https://archon.diy/docs/)
- Releases: v0.11.1 and v0.11.0 (2026-09-25), v0.10.x (08-30), v0.9.0 (08-17), v0.8.0 (08-06), v0.7.x (08-02/04), v0.6.0 (07-20), v0.5.0 (06-26); earlier 0.2.5 (2026-02-17) … 0.4.1 (05-28) in CHANGELOG — [Releases](https://github.com/coleam00/Archon/releases); [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md)
- Rewrite history: issue #952 (opened 2026-04-04, closed 04-07) migrated the TypeScript code from `dynamous-community/remote-coding-agent` into `coleam00/Archon`, archived the Python code, moved the license from a proprietary Dynamous license to MIT, published v0.2.13 and a Homebrew tap — [issue #952](https://github.com/coleam00/Archon/issues/952). Announcement #957 (2026-04-07, pinned): "entirely different product", "Nothing was deleted", "no migration path"; old code on `archive/v1-task-management-rag` — [issue #957](https://github.com/coleam00/Archon/issues/957)
- The archived v1 README: "Power up your AI coding assistants with your own custom knowledge base and task management as an MCP server"; FastAPI + Socket.IO, React/TS/Tailwind, Supabase (PostgreSQL + PGVector), PydanticAI agents; services Web UI 3737, API 8181, MCP 8051, agents 8052, optional "agent work orders" 8053 using the Claude Code CLI; "Archon is currently in beta!" — [archive branch](https://github.com/coleam00/Archon/tree/archive/v1-task-management-rag)
- Deployment today: single Docker container with SQLite ("Zero-config default. No database container needed"), compose profiles `with-db` (PostgreSQL 17), `cloud` (Caddy + HTTPS), `auth` (sidecar); image `ghcr.io/coleam00/archon:latest`; VPS guide (2–4 GB RAM); binaries via curl/PowerShell/Homebrew; Web UI 5173 / API 3090 in dev; multi-user only with Better Auth on PostgreSQL — [deployment docs](https://archon.diy/_llms-txt/deployment.txt)
- Unreleased: release binaries will stop bundling Claude/Codex/Pi (`archon provider add`); OpenCode and Copilot providers deprecated — [CHANGELOG Unreleased](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md)
- Third-party review (2026-04-13, v0.3.6): "heavily optimized for Claude Code", "early stage, rough edges", a PR-review workflow "can easily burn through $5-10 per run" — [andrew.ooo](https://andrew.ooo/posts/archon-ai-coding-workflow-engine-review/)
- Roadmap "Workflow Execution Reliability (#1378): Eliminate silent-success failure modes" and "Production-Ready Deployment (#1452)" are still planned — [roadmap](https://archon.diy/roadmap/)

### Inferences
- Three lineages share the name under this author: (a) a 2025 "agent that builds agents" phase (unverified, from memory of the repo's early history — repo created 2025-02-07), (b) the mid-2025 Supabase/MCP "command center" (now `archive/v1-task-management-rag`), (c) the April-2026 TypeScript workflow engine. Only (c) is live.
- Maturity: large community but pre-1.0 semantics; every minor since 0.9 lists multiple breaking YAML changes. Not solo-maintained (issues triaged by a second maintainer "Wirasm"; 3.5k forks), contrary to the earlier note's framing.

### Gaps
- No Supabase, RAG or knowledge-base feature exists in v2; I found no statement that it will be ported.
- Exact release date of the first TypeScript tag (v0.2.x, Feb 2026 under dynamous-community) is only inferred from the CHANGELOG.

## Q2. Core objects and whether it runs sessions itself

### Takeaway
Archon v2 runs coding-agent sessions itself (through the vendor SDKs, inside its own worktrees) and has no knowledge base, task board or MCP *server*; its objects are workflows, runs, nodes, projects (one persistent orchestrator chat per codebase), triggers, forges and workflow packs.

### Cited Findings
- Node kinds (one per node): `command:` / `prompt:` (AI), `bash:`, `script:` (bun or uv), `loop:`, `loop_group:` (multi-node sub-DAG), `approval:`, `wait:`, `cancel:`, `include:`, `workflow:` (child run); `provider:` per node (`claude`, `codex`, Pi) — [authoring guide](https://archon.diy/_llms-txt/guides.txt)
- Claude nodes run "through the Claude Agent SDK with the `claude_code` system prompt"; `settingSources` defaults to `['project','user']` (loads repo CLAUDE.md, skills, commands, agents); per-node `hooks:`, `mcp:` (path to MCP config JSON), `skills:`, `allowed_tools` / `denied_tools`, `effort:`; `persist_session: true` keeps a provider session across invocations; `context: fresh | shared | {resume: node-id}` — [authoring guide](https://archon.diy/_llms-txt/guides.txt)
- 0.9.0 breaking: "Claude workflow nodes see only the skills and MCP servers their YAML declares"; Unreleased: same for Codex, which now runs on `codex app-server` — [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md)
- Project object: v0.5.0 "Persistent Project Orchestrator: One stateful conversation per codebase, with memory across runs and restarts" — [roadmap](https://archon.diy/roadmap/)
- Shipped `sdlc` pack (`archon-ship`, `archon-plan`, `archon-review`, `archon-validate`; eleven workflows incl. `fix`, `upkeep`, `stabilize`); Web UI pages Runs, Run detail, Project chat, Settings, experimental Workflow builder (React Flow, since 0.2.7) — [README](https://github.com/coleam00/Archon); [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md)
- REST API at :3090: run/resume/approve/reject/cancel/abandon, `GET /api/runs/{id}/artifacts`, `POST /api/workflows/validate`, OpenAPI at `/api/openapi.json` — [reference docs](https://archon.diy/_llms-txt/reference.txt)
- Cole Medin's own framing (livestream): "think N8n, but for software development"; differentiators are worktree management, mixing providers, and controlling how context passes between sessions; shows plan → human approval → implement-in-fresh-context → validate → review → PR comment — [transcript](https://sozai.app/transcript/live-ai-coding-workflow-archon/index.md) (secondary)

### Inferences
- The only MCP relationship now is *consumption*: nodes declare MCP servers; nothing in README, docs or changelog describes Archon exposing an MCP server to editors. The brief's assumption ("serves context to sessions the user runs") describes v1 only.

### Gaps
- Whether Codex/Pi nodes honour repo-level AGENTS.md the way Claude nodes honour CLAUDE.md is not stated in the excerpt I read (AGENTS.md became "the canonical agent instruction file" in 0.9.0).

## Q3. Feature-by-feature check

### Takeaway
Present: DAG with trigger rules, per-node retries by error class, approval gates with durable waits, JSON-schema outputs, per-node and per-run cost, multi-harness per node, worktree + forge/PR integration. Partial: scheduling/triggers (macOS schedules + GitHub webhooks; signed webhooks unreleased), runtime pluggability (host or sibling Docker only), typed artifacts (JSON schema, not domain types). Absent: sub-graph retry with attempt lineage, content-bound approvals, runtime/VCS protocols, Python DSL.

### Cited Findings
- **DAG** — present: `depends_on`, same-layer nodes run concurrently, `trigger_rule: all_success | one_success | none_failed_min_one_success | all_done`, `when:` expressions (no parentheses), `include:` with `fan_out:`, `workflow:` sub-runs with per-child worktrees (0.8.0), DAG graph viewer (0.2.12) — [guides](https://archon.diy/_llms-txt/guides.txt); [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md)
- **Scheduling/triggers** — partial: 0.11.0 "Trigger bindings start governed runs from native macOS schedules and verified GitHub webhooks. Queued work survives restarts"; Unreleased "Webhook triggers … Standard Webhooks v1", "Typed inputs: trigger bindings can map event fields only into inputs a workflow declares"; CLI `archon trigger fire|drain|list|inspect|execute|withdraw|schedule`; cron is not named in the changelog — [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md); [reference](https://archon.diy/_llms-txt/reference.txt)
- **Retries** — present, node-level only: `retry: {max_attempts: 1-5, delay_ms: 1000-60000 (doubles), on_error: transient|all}`; AI nodes default to 2 retries; classes FATAL / TRANSIENT / UNKNOWN; Unreleased "Retry decisions depend only on the recorded failure class"; `loop_group` repeats a sub-DAG with `$LOOP_PREV.<node>.output`, `until_field`, `max_iterations`; a `failed` run can be resumed, `cancelled` is discarded (0.11.0) — [guides](https://archon.diy/_llms-txt/guides.txt); [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md)
- **Approval gates** — present: `approval:` node; gates "produce `{decision, text}` output" (0.10.0); `wait: attention` with declared `form` (message/approval/select/multi-select) and an attention queue (Unreleased); approve/reject via CLI, HTTP, Web UI, chat (a plain chat message no longer counts as approval, 0.10.0). Open issue #4162 "HTTP /approve and /reject cannot refuse a stale answer" — [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md); [open issues](https://github.com/coleam00/Archon/issues?q=is%3Aissue+is%3Aopen+sort%3Aupdated-desc)
- **Typed artifacts** — partial: `output_format` is a JSON Schema enforced by the SDK on Claude/Codex, best-effort on Pi; `$node.output.field`; `returns:` + `outcome_field` (boolean success); 0.5.0 "typed artifacts", 0.10.0 "Structured results keep their type across persistence, resume and `with:` bindings"; `$TYPED_ARTIFACTS_FILE`, `$ARTIFACTS_DIR`; outputs >32 KB spill to file; `inputs:` "is not a typed JSON schema" — [guides](https://archon.diy/_llms-txt/guides.txt); [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md)
- **Cost** — present: 0.10.0 "Transcripts record per-node and run-level cost and tokens"; 0.11.0 "Run totals and cost are recorded on terminal runs"; `concurrency.providers` caps attempts per provider — [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md)
- **Runtime pluggability** — partial/absent: runs execute on the host; `--container` launches "a sibling Docker container per run" (folder projects, needs host Docker socket, "does not work inside the Docker Compose stack"); Kubernetes/remote execution not mentioned — [deployment](https://archon.diy/_llms-txt/deployment.txt); [adapters](https://archon.diy/_llms-txt/adapters.txt)
- **Git/PR** — present: worktree per run (`worktree.enabled`, `mutates_checkout`), `--base` override, forge plugins "handle PR checks, and with `ARCHON_SDLC_FORGE=forge` they also create and update PRs"; `archon forge pr.create|pr.edit-body|pr.ready|checks|comment.upsert`; GitHub App auth, GitLab and Gitea adapters; `wait: {event: checks.complete}` woken by check-run webhooks — [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md); [adapters](https://archon.diy/_llms-txt/adapters.txt)
- **Multi-harness** — present: Claude Agent SDK, Codex SDK, Pi per node; OpenCode/Copilot deprecated; model `tiers:` only built-in for claude and codex (0.10.1); roadmap "Pluggable provider SDKs beyond Claude and Codex" and "Local LLM Support (#1449)" — [CHANGELOG](https://raw.githubusercontent.com/coleam00/Archon/dev/CHANGELOG.md); [roadmap](https://archon.diy/roadmap/)

### Inferences
- Archon's retry is Temporal-style (same node, same inputs); kora's sub-graph retry with `previous_attempt` inputs maps only loosely onto `loop_group` + `$LOOP_PREV`, which is an iteration loop, not an attempt object with retained per-attempt artifacts.
- Approvals are decision objects on a run, not bound to a commit SHA or artifact hash; #4162 confirms stale answers are currently accepted.
- Harness access is SDK-based, so harness version pinning and CLI-surface isolation (kora FR-3.3) are the SDK's concern; Pi is the only non-vendor harness.

### Gaps
- Could not confirm whether `wait: attention` gates have timeouts with defaults (kora FR-7) beyond `deadline_ms` on event waits.
- Did not verify cron-expression support in trigger bindings on Linux (only "native macOS schedules" is documented).

## Q4. Why the earlier note listed Archon, and is it accurate

### Takeaway
The earlier note took Archon from the awesome-agent-orchestrators entry; the DAG + approvals + validation-gates claim is accurate for Archon v2, but "small solo-maintained" is not.

### Cited Findings
- awesome-agent-orchestrators entry (Agent Infrastructure & Primitives): "Harness builder for deterministic AI coding workflows, combining agent steps with scripts, validation gates, approvals … Claude Code, Codex, and more." — [awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- Our note paraphrased it as "deterministic workflows with validation gates and approvals" and grouped Archon with Bernstein, Crewplane, tutti, Open Multi-Agent, kandev under "small solo-maintained OSS" — `market_positioning_and_gaps.md` (local)
- Evidence of DAG, gates, structured outputs: Q3 above. Evidence of scale: 23.7k stars, 3.5k forks, 3,644 commits — [GitHub API](https://api.github.com/repos/coleam00/Archon)

### Inferences
- The feature attribution was correct; the size and maturity attribution was wrong. Archon should be moved out of the "small projects" bucket and treated as the most-adopted open-source DAG-of-coding-agent-sessions engine.

### Gaps
- None material.

## Q5. Overlap, difference, what kora can borrow

| Capability | Archon v2 (Oct 2026) | kora (FR v0.3) |
|---|---|---|
| Node = full coding-agent session | yes, via Claude Agent SDK / Codex SDK / Pi | yes, via harness CLI (`claude --bare -p`, `codex exec`) |
| Multi-harness per node | yes (claude, codex, pi) | yes (claude, codex; later OpenCode, Pi) |
| Graph definition | hand-written YAML; experimental visual builder | Python DSL → versioned YAML IR + JSON Schema, `kora validate` |
| Persisted DAG, trigger rules | yes (4 rules), `when:`, `include`/`fan_out`, sub-runs | yes (6 Airflow rules), fan-out, sub-graphs |
| Retries | node-level, 1–5 attempts, error classes | node and sub-graph, `previous_attempt` inputs, per-attempt artifacts |
| Approval gates | `approval:` / `wait: attention` forms, attention queue, chat/Web/HTTP | protocol channel, typed form, timeout + default, bound to commit SHA |
| Typed artifacts | JSON-schema outputs, typed values, `$ARTIFACTS_DIR` | domain types: StructuredOutput, VcsRef, AgentSession, CommandResult, ReviewBundle |
| Deterministic gate nodes | `bash:`, `script:`, `cancel:` | command nodes |
| Isolated workspace | git worktree per run / per child; optional sibling Docker | fresh checkout per node via VCS protocol; runtime protocol |
| Runtime pluggability | host or `--container` (Docker); no K8s/remote | local, Docker [MVP]; sandbox, K8s [post-MVP]; protocol |
| VCS pluggability | git only; forges GitHub/GitLab/Gitea | VCS protocol (git, a non-git VCS) |
| Secrets | `.env` vault, per-user credentials, GitHub App tokens | secrets protocol, references only in IR |
| Cost accounting | per-node + run tokens/USD in transcripts; provider concurrency caps | tokens per model per attempt, price table, billing class, soft USD cap |
| Triggers | manual, chat, GitHub events, macOS schedules, signed webhooks (unreleased) | manual, cron, webhook, sensors |
| Repo config trust | `settingSources` default project+user; declared-only skills/MCP since 0.9 | `trust_repo_config` flag |
| Chat adapters | Slack, Telegram, Discord, GitHub comments | post-MVP |
| Distribution | MIT, workflow packs/marketplace, binaries | Apache-2.0, Python package |

Borrowable from Archon (all sourced above): (1) error-class-only retry decisions ("never retry config errors"); (2) `outcome_field` separating business outcome from lifecycle status; (3) `wait: attention` with a declared answer form, readable by the next node; (4) `trigger_rule: none_failed_min_one_success`; (5) `--dry-run` simulation without providers; (6) "declared-only" skills/MCP scoping per node as the secure default; (7) workflow packs installable from GitHub with declared `inputs:`/`returns:` signatures; (8) `failed` (resumable) vs `cancelled` (discarded) run semantics; (9) evidence gate (0.7.0) and CI-aware correction rounds in the SDLC pack as a reference pipeline. The v1 idea (MCP server exposing project knowledge to any editor) is abandoned by Archon and is not something kora should adopt as core.

## Q6. Positioning implication

### Takeaway
Archon v2 is a **direct competitor** on the "YAML DAG of coding-agent sessions with gates" claim and the strongest OSS incumbent for it; it is not a complement (no MCP context server exists any more). kora's defensible differences are the control-plane primitives Archon lacks: attempt objects and sub-graph retry with lineage, content-bound approvals, domain-typed artifacts (VcsRef/ReviewBundle), runtime/VCS/secrets protocols for enterprise runtimes, Python DSL compiled to a validated IR, and cron/sensor scheduling as a first-class scheduler rather than a chat-first orchestrator.

### Cited Findings
- Archon self-positions as "harness builder", "Dockerfiles for infrastructure and GitHub Actions for CI/CD", "n8n for software development" — [issue #957](https://github.com/coleam00/Archon/issues/957); [transcript](https://sozai.app/transcript/live-ai-coding-workflow-archon/index.md)
- Its roadmap priorities are control-flow expressiveness (#1333: multi-node loop bodies, branching on approve/reject, expression evaluator), evals, local LLMs, reliability, multi-repo, enterprise GitHub auth, production deployment — [roadmap](https://archon.diy/roadmap/)

### Inferences
- The positioning doc's claim "nobody ships a persisted, user-defined DAG whose nodes are full coding-agent sessions on the user's own harness" needs qualification: Archon ships exactly that (YAML, SDK-based, worktree-isolated) with 23.7k stars. kora's claim must narrow to the determinism-layer primitives listed above and to enterprise runtime/VCS pluggability.
- Archon is SDLC-shaped (issue → plan → implement → review → PR, chat-driven); kora's canonical use case (baseline → hypotheses → fan-out candidates → metrics gates → evidence-backed PR) exercises exactly the primitives Archon does not model (attempt lineage, metrics-typed artifacts, select/fan-in by threshold). That is the comparison to make in positioning.md.
- Interop option: kora could execute an Archon workflow pack as a node (via the REST API `POST /api/workflows/{name}/run` + `/runs/{id}/artifacts`) if a team already has Archon packs — low priority, but cheap to document.

### Gaps
- No usage/adoption numbers beyond GitHub stars; no evidence on how many teams run Archon unattended on schedules versus interactively from chat.
- Cole Medin's YouTube videos were only read through a third-party transcript summary; the demo video URL was not retrieved.
