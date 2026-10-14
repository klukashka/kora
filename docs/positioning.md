# kora — Product Positioning

Status: draft v0.2 (2026-10-15). Decisions in this document were taken by the project owner; research backing is in `reports/` and `research_notes/`.

## One-liner

**kora is an open-source orchestrator for graphs of coding-agent sessions.** Think Airflow, but the unit of work is a full Claude Code or Codex session with its own skills, subagents, hooks and MCP servers, and the graph adds what a single session cannot: approval gates, retries of nodes and sub-graphs, typed artifacts between nodes, cost accounting and pluggable runtimes.

Tagline: **Orchestrate coding-agent sessions as a graph.**

"Airflow for agentic pipelines" stays as the one-line analogy for people who know Airflow; it is not the tagline.

## The problem

Teams already run many coding-agent sessions to do real engineering work: periodic query optimisation, dependency upgrades, test-flake repair, migrations. Every published production pipeline of this kind (Google, Airbnb, Anthropic, Dropbox, Stripe, Uber) has the same shape: deterministic targeting, bounded per-unit agent attempts, mechanical validation gates, batched human review, slow rollout. Every one of them was built bespoke.

The tooling market splits into three layers that do not meet:

1. **Vendor cloud sessions** (Claude Code Routines, Codex automations, Cursor Automations, Google Jules scheduled tasks, Devin). One prompt → one session → one PR. No DAG, no approval between stages, no typed artifacts, no cross-run cost view, one vendor's agent only.
2. **Workflow orchestrators** (Airflow 3.3 + `common-ai`, Prefect, Dagster, Kestra, Flyte, Temporal). Mature DAGs, retries and human-in-the-loop, but an "agent" is an LLM tool loop inside one task. No repo, branch, PR or session semantics.
3. **"Run many Claude Codes" tools** (Gas Town, Paperclip, Claude Squad, Conductor, ~260 others). Mostly tmux, worktree and kanban managers. DAG + typed artifacts + approvals appears only in solo-maintained projects.

Nobody ships a persisted DAG whose nodes are full coding-agent sessions. The phrase "Airflow for agents" is unclaimed as a brand.

## The thesis: a determinism layer over an agentic layer

A coding agent is excellent at open-ended, non-deterministic work inside a bounded task: research a codebase, form hypotheses, try a change, fix review comments. It is bad at being the control loop: it forgets, over-spends, retries blindly, and cannot be audited.

kora keeps the two apart:

- **Outer loop is deterministic and owned by kora**: graph topology, dependency rules, retries, timeouts, approvals, budgets, artifacts, scheduling, and cheap mechanical gates (build, tests, benchmarks) as command nodes. No model in the coordination loop.
- **Inner loop is the harness's own**: subagents, tool permissions, compaction, skills. kora does not re-implement it; it configures it per node and consumes its result.

This is the same split Anthropic ("workflows vs agents"), 12-factor agents and the published case studies converge on. kora is the reusable version of the outer loop.

## Who it is for

Primary user: **one engineer who both writes and operates graphs** (a developer or data engineer automating recurring engineering work on their own repositories). The platform-team scenario (presets catalogue, quotas, RBAC) is a later layer, but the architecture must not block it.

Distribution: **public open source, Apache-2.0**, general purpose. Enterprise adopters (including the author's employer) plug in their own runtime, VCS and secrets provider through documented protocols rather than forking.

## What makes kora different

| Capability | Vendor cloud sessions | Orchestrators | OSS multi-agent tools | kora |
|---|---|---|---|---|
| Node = full coding-agent session | yes (single) | no (LLM loop in a task) | yes | **yes, as a graph node** |
| Harness-agnostic (Claude Code, Codex, later OpenCode, Pi) | no | n/a | partly | **yes** |
| Persisted DAG with dependency rules | no | yes | rarely | **yes** |
| Approval gate between stages, typed, with timeout and default | no (per-tool only) | yes | rarely | **yes, pluggable channel** |
| Retry policy per node and per sub-graph | no | node only | no | **node and sub-graph** |
| Typed artifacts on edges (structured output, git ref, session) | no | state / XCom | PRs, text | **yes, extensible** |
| Deterministic gate steps (build, tests, benchmark) outside the agent loop | no | yes | no | **yes, command nodes** |
| Pluggable runtime (local, Docker, sandbox, K8s, enterprise CI) | no | yes | partly | **yes, protocol** |
| Pluggable VCS (git, arc) | git only | n/a | git only | **yes, protocol** |
| Cost accounting per node / run | partial | no | partial | **tokens + price table, soft USD cap** |
| Code-first DSL compiled to a versioned IR | no | Airflow only | no | **Python → YAML IR** |

## Primary contrast: vendor cloud sessions

kora positions first against Claude Code Routines, Codex automations and Cursor Automations, because that is where the target user is today. The pitch:

- *You already run scheduled agent sessions. kora lets you chain them, approve between them, retry the failed stage instead of the whole thing, see what each stage cost, and keep the whole pipeline in your repo as code.*
- *Your pipeline is not locked to one vendor's agent. Swap Claude Code for Codex per node.*
- *Your pipeline runs where you want: your laptop, Docker, a cloud sandbox, your company's CI.*

Secondary contrasts:

- **vs Airflow `common-ai` / Prefect / Dagster**: they orchestrate LLM calls inside data tasks; kora orchestrates sessions that touch repositories and produce PRs. kora can be triggered from and trigger Airflow; it does not replace it for ETL.
- **vs LangGraph / Temporal style graphs**: those are graphs of LLM calls or durable functions and require writing deterministic workflow code around every model call; kora nodes are whole sessions and the deterministic code is the graph itself.
- **vs Gas Town / Paperclip**: those coordinate long-lived agent "workers" with chat and merge queues; kora is a scheduler with a persisted graph, typed edges and audit, not an org chart.

## Canonical use case

Recurring optimisation of YQL (Yandex Query Language) queries in data pipelines:

1. *Find target* — an agent session scans the pipeline graph, picks a slow query, studies the code and task statement. Output: structured target description.
2. *Form hypotheses* — an agent session proposes N optimisation approaches. Output: list of hypotheses.
3. *Fan-out* — one sub-graph per hypothesis: agent implements it on an isolated checkout → produces a branch/PR → a command node runs the benchmark deterministically → review session evaluates diff and benchmark → loop back with feedback (new session with the PR and comments as input) until the PR is valid or attempts are exhausted. Sub-graph has its own retry and budget.
4. *Approval gate* — a human reviews the surviving PR(s). Approval arrives through a pluggable channel (CLI/API, PR approval in the VCS host, minimal web page).
5. *Rollout to pre-stable* — a node waits for the external signal/timer and continues.

Everything above the node level is deterministic and visible; everything inside a node is the agent's.

## Non-goals

- **Not a framework for writing agents.** No LLM-call graph, no tool loop, no prompt library. The harness owns the inner loop.
- **Not a general task runner.** Command nodes exist for mechanical gates around agent sessions, not for running arbitrary Python pipelines; that is what data orchestrators are for.
- **Not a sandbox provider.** Isolation comes from Docker, cloud sandboxes, Kubernetes or an enterprise runtime through the runtime protocol.
- **Not a data orchestrator.** kora does not schedule ETL; it interoperates with Airflow and friends.

## Risks and watch-list

- Vendors absorb pipeline features into their harnesses (Claude Code dynamic workflows, Managed Agents workflow runs). kora's defence: cross-harness, cross-run, approval-gated, cost-attributed composition that a single-vendor runtime cannot offer.
- Harness CLI surfaces drift (`--bare` becoming default, JSONL event schemas marked unstable). kora pins harness versions per node and isolates them behind a harness adapter protocol.
- The merged Prefect–Dagster entity or Airflow ships a session-level node first. kora's defence: harness-agnostic, VCS-agnostic, runtime-agnostic from day one.
- Human review remains the throughput ceiling (AI-authored PRs wait 4.6× longer for review). kora must make review artifacts batched and legible, not merely produce more PRs.
