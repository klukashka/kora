# kora — Product Positioning

Status: draft v0.3 (2026-10-15). Decisions in this document were taken by the project owner; research backing is in `reports/` and `research_notes/`.

## One-liner

**kora is an open-source orchestrator for graphs of coding-agent sessions.** Think Airflow, but the unit of work is a full Claude Code or Codex session with its own skills, subagents, hooks and MCP servers, and the graph adds what a single session cannot: approval gates, retries of nodes and sub-graphs, typed artifacts between nodes, cost accounting and pluggable runtimes.

Tagline: **Orchestrate coding-agent sessions as a graph.**

"Airflow for agentic pipelines" stays as the one-line analogy for people who know Airflow; it is not the tagline.

## The problem

Teams already run many coding-agent sessions to do real engineering work: periodic query optimisation, dependency upgrades, test-flake repair, migrations. Every published production pipeline of this kind (Google, Airbnb, Anthropic, Dropbox, Stripe, Uber) has the same shape: deterministic targeting, bounded per-unit agent attempts, mechanical validation gates, batched human review, slow rollout. Every one of them was built bespoke.

The tooling market splits into three layers that do not meet:

1. **Vendor cloud sessions** (Claude Code Routines, Codex automations, Cursor Automations, Google Jules scheduled tasks, Devin). One prompt → one session → one PR. No DAG, no approval between stages, no typed artifacts, no cross-run cost view, one vendor's agent only.
2. **Workflow orchestrators** (Airflow 3.3 + `common-ai`, Prefect, Dagster, Kestra, Flyte, Temporal). Mature DAGs, retries and human-in-the-loop. Most model an "agent" as an LLM tool loop inside one task. The exception is Airflow's `AnthropicAgentSessionOperator` (provider 0.1.0, July 2026; deferrable, usage and cost to XCom), which runs a whole server-side **Managed Agents** session, but not a local coding harness and not a repository checkout. Kestra documents retrying a sub-flow as a unit and (in the unreleased `RETRY_FLOWABLE` mode) a whole task group. None of them has a session handle, a workspace or a change-request as a first-class object.
3. **"Run many Claude Codes" tools** (Gas Town, Paperclip, Claude Squad, Conductor, ~260 others). Mostly tmux, worktree and kanban managers. DAG + typed artifacts + approvals appears only in solo-maintained projects.
4. **Software factories** (LangChain Open SWE, Stripe Minions, Dropbox Nova, Factory.ai). End-to-end autonomous coding agents with a fixed pipeline: investigate, change, validate, open PR, react to review, babysit CI. Open SWE is the closest open-source one; it is built on Deep Agents, has no user-defined graph, no stage gates, no retry configuration and no multi-harness notion, and is currently closed to external contributions.

Nobody ships a persisted, user-defined DAG whose nodes are full coding-agent sessions on the user's own harness. The phrase "Airflow for agents" is unclaimed as a brand.

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
| User-defined pipeline (vs fixed factory pipeline) | no | yes | partly | **yes** |

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

Recurring optimisation of YQL (Yandex Query Language) queries in ETL tasks of an Airflow-like data-pipeline framework over YTsaurus. Today a human does it like this: run the ETL task in dev mode against production tables (writing to a dev area) and record all operation metrics as the **baseline**; form hypotheses about individual pieces of the query; measure the pieces; assemble the final query and run the same dev-mode task again; the **diff between the two dev runs is the profit or loss**. The result is a PR.

As a kora graph the process is a background job with exactly three human touchpoints: manually triggering a specific target, approving the final PR, and handling system errors.

1. *Find target* — agent session scans the pipeline, picks a slow task, studies code and task statement. Output: structured target.
2. *Baseline* — command node runs the dev-mode task and captures metrics. Output: `MetricsReport` (baseline).
3. *Hypotheses* — agent session proposes N approaches. Output: list of hypotheses.
4. *Fan-out* — one sub-graph per hypothesis: agent implements it on an isolated checkout → command node runs the dev-mode task on the candidate → command node checks result equivalence against the baseline run → agent review session evaluates diff and metrics → attempt either succeeds or fails with a reason; the sub-graph retries with the previous attempt's artifacts and feedback as inputs until it succeeds or its attempts, budget or timeout are exhausted.
5. *Select* — branch/fan-in picks the candidate(s) that beat the baseline by the configured threshold; mutually exclusive candidates for one query are reduced to one.
6. *Review bundle and PR* — kora attaches the evidence to the PR: diff, baseline vs candidate metrics, gate results, why this candidate, cost of all attempts. The human's only job is to read it and approve.
7. *Rollout to pre-stable* — a node waits for the deploy signal, then a timer and a metrics check confirm the gain on real load.

**Success criterion** for this graph is deterministic: candidate dev run produces equivalent results and improves the recorded metrics by at least the threshold, confirmed after rollout. Agents propose; command nodes measure; kora decides and records.

### Other workloads the owner's teams want on kora

- **Propagating a field from the raw layer to DWH data marts**: canonicalisation, sampling, test runs, deterministic checks, PR.
- **ETL graph analysis**: find duplicated sub-graphs or processes across hundreds of DAGs and propose a more efficient restructuring; this is optimisation at the graph level, not the task level.

kora itself is domain-agnostic. A frontend or DevOps engineer should be able to build their own graph (dependency upgrades, flaky-test repair, lint migrations) from the same node kinds and protocols; the data-engineering examples are the owner's own pilots, not the product boundary.

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
- Human review remains the throughput ceiling (AI-authored PRs wait 4.6× longer for review). kora's answer is to reduce the human touchpoints to one evidence-backed PR approval per target (the review bundle), not to produce more PRs.
- Software factories (Open SWE, Factory.ai) may add user-defined graphs and multi-harness support, collapsing the gap from the other side.

## Evidence, hypotheses and open risks

What is backed by evidence today:

- **The problem is real for the owner.** The owner personally runs several dozen agent-driven tasks; their team's Airflow-like ETL framework serves ~300 authors per week, ~400 graphs and tens of thousands of scheduled processes; each DWH has ~5 data engineers/analysts developing ETL full time, and there are several dozen such DWHs. The YQL optimisation routine described above is done by hand today.
- **The pipeline shape is proven elsewhere.** Google, Airbnb, Anthropic, Dropbox, Stripe and Uber all built the same deterministic-outer-loop shape bespoke (see `reports/agentic-pipeline-orchestrators.md`).
- **The gap is real as of October 2026.** No surveyed product offers a user-defined DAG of coding-agent sessions on the user's own harness with stage approvals, sub-graph retry, typed artifacts and pluggable runtime; the closest pieces are verified above.

What is a hypothesis, not yet validated:

- **H1. Demand beyond the owner's organisation.** Other single engineers and teams want this enough to adopt a new control plane. Test: public pilots after MVP; count graphs authored by people other than the author.
- **H2. Mixing harnesses per node has value.** Test: in the pilot, measure how often a graph uses more than one harness and whether it changes outcomes or cost.
- **H3. An own scheduler is necessary.** The alternative is an existing engine (Temporal, Kestra, Airflow, CI) plus a session adapter: a whole coding session as one activity/task, with the engine owning retries, waits and approvals. This has not been tried. The owner chose to build the control plane because the enterprise runtime target (Arcadia CI / Sandbox / Tasklets) and VCS (arc) are not reachable from those engines, and because artifact typing, sub-graph retry with attempt lineage and evidence-backed approvals cut across engine primitives. Test: before MVP freeze, express the YQL graph in one existing engine with a minimal adapter and list the limitations an adapter cannot remove; record the result in this document. If the list is short, kora becomes a library over that engine.
- **H4. The value is in the outer loop, not in candidate generation.** Harnesses will keep absorbing single-session parallelism (dynamic workflows, Managed Agents, Cursor subagents). Test: pilot metrics below.

Pilot metrics (to be collected on 20–30 real targets before the first public release): accepted useful changes per target; human minutes per accepted change; full cost per accepted change including failed attempts and fan-out losers; maintenance effort of the graph and the platform; comparison against the same work done by hand or via a vendor scheduled session.
