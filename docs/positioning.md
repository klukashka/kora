# kora — Product Positioning

Status: draft v0.8 (2026-10-15). Decisions in this document were taken by the project owner; research backing is in `knowledge/research/`.

## One-liner

**kora is an open-source platform for running graphs of coding-agent sessions at organisation scale.** The unit of work is a full Claude Code or Codex session with its own skills, subagents, hooks and MCP servers. The platform adds what neither a single session nor a single-repo workflow engine provides: a scheduler-grade control plane (cron, sensors, namespaces, hundreds of concurrent sessions), attempts and artifacts as first-class objects with lineage, approvals bound to content, and protocols that let it run on an organisation's own runtime, VCS and secrets infrastructure.

Tagline: **Orchestrate coding-agent sessions as a graph.**

"Airflow for agentic pipelines" stays as the one-line analogy for people who know Airflow; it is not the tagline.

## The problem

Teams already run many coding-agent sessions to do real engineering work: periodic query optimisation, dependency upgrades, test-flake repair, migrations. Every published production pipeline of this kind (Google, Airbnb, Anthropic, Dropbox, Stripe, Uber) has the same shape: deterministic targeting, bounded per-unit agent attempts, mechanical validation gates, batched human review, slow rollout. Every one of them was built bespoke.

The tooling market splits into three layers that do not meet:

1. **Vendor cloud sessions** (Claude Code Routines, Codex automations, Cursor Automations, Google Jules scheduled tasks, Devin). One prompt → one session → one PR. No DAG, no approval between stages, no typed artifacts, no cross-run cost view, one vendor's agent only.
2. **Workflow orchestrators** (Airflow 3.3 + `common-ai`, Prefect, Dagster, Kestra, Flyte, Temporal). Mature DAGs, retries and human-in-the-loop. Most model an "agent" as an LLM tool loop inside one task. The exception is Airflow's `AnthropicAgentSessionOperator` (provider 0.1.0, July 2026; deferrable, usage and cost to XCom), which runs a whole server-side **Managed Agents** session, but not a local coding harness and not a repository checkout. Kestra documents retrying a sub-flow as a unit and (in the unreleased `RETRY_FLOWABLE` mode) a whole task group. None of them has a session handle, a workspace or a change-request as a first-class object.
3. **"Run many Claude Codes" tools** (Gas Town, Paperclip, Claude Squad, Conductor, ~260 others). Mostly tmux, worktree and kanban managers. The exception, and the closest competitor, is **Archon** (coleam00/Archon, MIT, ~23.7k stars, v0.11.1 of 2026-09-25): a TypeScript/Bun YAML workflow engine whose nodes are Claude Agent SDK, Codex SDK or Pi sessions plus bash/script nodes, each run in its own git worktree, with `depends_on`, trigger rules, `fan_out`, included sub-workflows, node-level retries by error class, approval/attention gates, per-node token and USD cost, forge plugins that open PRs on GitHub/GitLab/Gitea, GitHub-webhook triggers, a web console, Slack/Telegram entry points, and single-container deployment on SQLite. It is SDLC-shaped (issue → plan → implement loop → tests → review → approve → PR) and positioned as "n8n for software development".
4. **Software factories** (LangChain Open SWE, Stripe Minions, Dropbox Nova, Factory.ai). End-to-end autonomous coding agents with a fixed pipeline: investigate, change, validate, open PR, react to review, babysit CI. Open SWE is the closest open-source one; it is built on Deep Agents, has no user-defined graph, no stage gates, no retry configuration and no multi-harness notion, and is currently closed to external contributions.

Archon does ship a user-defined DAG of coding-agent sessions on the user's own harness. What nobody ships, Archon included, is the orchestration layer around it: attempts as first-class objects with per-attempt artifacts and lineage, retry of a whole sub-graph with the previous attempt's outputs as inputs, approvals bound to a commit SHA and gate digests with timeout and default, domain-typed artifacts (change request, metrics report, evidence bundle) on edges, a scheduler with cron and sensors, pluggable runtime, VCS and secrets protocols for enterprise environments, and a code-first DSL compiled to a validated IR. The phrase "Airflow for agents" is unclaimed as a brand.

## The thesis: a determinism layer over an agentic layer

A coding agent is excellent at open-ended, non-deterministic work inside a bounded task: research a codebase, form hypotheses, try a change, fix review comments. It is bad at being the control loop: it forgets, over-spends, retries blindly, and cannot be audited.

kora keeps the two apart:

- **Outer loop is deterministic and owned by kora**: graph topology, dependency rules, retries, timeouts, approvals, budgets, artifacts, scheduling, and cheap mechanical gates (build, tests, benchmarks) as command nodes. No model in the coordination loop.
- **Inner loop is the harness's own**: subagents, tool permissions, compaction, skills. kora does not re-implement it; it configures it per node and consumes its result.

This is the same split Anthropic ("workflows vs agents"), 12-factor agents and the published case studies converge on. kora is the reusable version of the outer loop.

## AI-first

kora is operated by agents as much as by people. Everything a human can do through the UI, CLI or API, an agent can do through kora's own MCP server and a published skill set: author a graph in the DSL, compile and validate it with machine-readable errors, deploy it, trigger a run, inspect runs, logs, artifacts and cost, answer an approval, retry a node, cancel a run, register a preset. The API is the single source of capability; UI, CLI, MCP tools and skills are four equal clients of it, and a capability that exists in one must exist in all. Consequences for the design: every operation is idempotent and addressable by stable ids; every error is structured; every artifact is readable by an agent without the UI. The reverse does not hold: an agent session running as a node inside a graph has no access to kora; the MCP server and the skills exist for the engineer's own session, which operates kora with the engineer's token and permissions.

## Who it is for

Primary users (decided 2026-10-15, replacing the earlier "one engineer" persona after the Archon assessment):

- **A platform team** that installs kora once for an organisation, implements the runtime, VCS and secrets protocols for its infrastructure, publishes node presets, and operates the control plane.
- **Engineers in product and data teams** (data engineers, frontend, DevOps) who author graphs in Python on those presets and own the outcomes of their pipelines. They never operate the platform.

A single engineer can still run `kora standalone` on a laptop, but that is a development convenience, not the design centre. For a single developer on GitHub with one repository, Archon is the better tool today and kora does not try to beat it there.

Distribution: **public open source, Apache-2.0**, general purpose. Every protocol and reference implementation is in the open-source core; adopters (including the author's employer, where the VCS is arc and the CI is Arcadia) plug in their own infrastructure without forking.

## What makes kora different

| Capability | Vendor cloud sessions | Orchestrators | OSS multi-agent tools | kora |
|---|---|---|---|---|
| Node = full coding-agent session | yes (single) | no (LLM loop in a task) | yes | **yes, as a graph node** |
| Harness-agnostic (Claude Code, Codex, and ACP agents such as OpenCode, Pi, Gemini) | no | n/a | partly | **yes** |
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
| Agent-operable with full parity (MCP server + skills for every human operation) | partly (CLI) | no | partly (Archon ships a skill to run workflows) | **yes, by principle** |

Archon deserves its own comparison because it is the only system in the third column with a real DAG:

| Capability | Archon v0.11 | kora |
|---|---|---|
| Node = coding-agent session on the user's harness | yes (Claude Agent SDK, Codex SDK, Pi) | yes (Claude Agent SDK, Codex SDK, and any ACP agent) |
| Deterministic nodes | bash / script | command node with typed `CommandResult` |
| Graph definition | hand-written YAML in `.archon/workflows/`, breaking changes in every minor since 0.9 | Python DSL compiled to a versioned, schema-validated YAML IR |
| Isolation | git worktree per run | isolated checkout per node through a VCS protocol (git in core, arc by adopter) |
| Loops | `loop: until: <condition>` judged inside the agent, `fresh_context` | sub-graph retry with explicit attempts, `previous_attempt` inputs, attempt lineage |
| Retry | node-level, 1–5 attempts by error class | node and sub-graph policies with `retry_on` classes, three strategies |
| Approval | `interactive`/attention gate with answer forms; not bound to content (stale answers accepted, issue #4162) | gate bound to commit SHA + gate digests, timeout and default, pluggable channel |
| Artifacts | JSON-schema `output_format`, `outcome_field` | typed artifacts with versions and lineage: `StructuredOutput`, `VcsRef`, `AgentSession`, `CommandResult`, `ReviewBundle` |
| Scheduling | GitHub webhooks, macOS schedules (0.11); no cron documented | manual, cron, webhook, sensors; control plane with namespaces |
| Runtime | host process or sibling Docker container | runtime protocol: local, Docker, then cloud sandbox, K8s, enterprise CI |
| Cost | per-node and per-run tokens/USD | token ledger, price table, soft budget, usage completeness, billing class |
| Deployment | single container, SQLite; Postgres/auth profiles | standalone SQLite; docker-compose with Postgres |
| Target scale | single developer or small team | hundreds of concurrent sessions, dozens of teams |
| Agent operability | a Claude Code skill triggers workflows and reports results | MCP server and skills with full parity: author, compile, deploy, trigger, inspect, approve, retry, cancel |

## Primary contrast: Archon

kora positions first against Archon, because it is the only other open-source system where a graph node is a full coding-agent session on the user's own harness, and because anyone evaluating kora will find Archon first (~23.7k stars). Archon's model: a hand-written YAML workflow per repository, run in a git worktree on one host, with agent, bash and loop nodes, an interactive approval and a PR at the end; the loop's exit condition is judged by the agent and the approval is a conversation. It is the right tool for one developer encoding one repository's SDLC, and kora concedes that ground.

kora's model: a platform. Graphs are Python compiled to a validated IR and registered in a control plane that schedules them on cron, webhooks and sensors across teams and runtimes. The split in one sentence: **Archon is a workflow engine for a repository; kora is the orchestration platform for an organisation.** The differences, ordered by how hard they are to copy:

- *It runs on the organisation's infrastructure.* Runtime, VCS and secrets are protocols with conformance suites: local and Docker in the core, cloud sandboxes and Kubernetes next, and enterprise CI (Arcadia CI, Sandbox, Tasklets) and non-git VCS (arc) by the adopter. Archon assumes git worktrees and a host or sibling container.
- *It is a control plane.* Namespaces, concurrency limits, cron and sensors, Postgres, crash adoption, hundreds of concurrent sessions across dozens of teams. Archon runs on one host for one developer.
- *Attempts are objects.* A failed sub-graph is retried as a unit with the previous attempt's artifacts and feedback as inputs, every attempt's artifacts are kept with lineage, and the cost of all attempts is attributed to the accepted change. Archon has node-level retries and an agent-judged loop.
- *Approvals are bound to content.* A kora approval records the commit SHA and the gate digests it covers; a new push invalidates it. Archon's attention gate accepts answers for whatever is current.
- *Artifacts have domain types.* Change request, metrics report, command result, session, evidence bundle: typed edges the scheduler can branch on and the reviewer can read. Archon passes JSON-schema outputs.
- *Deterministic gates are nodes, not prompts.* Baseline measurement, equivalence check and benchmark run as command nodes whose results drive the graph; the agent never decides whether it succeeded.

Coexistence: a kora node can run an Archon workflow pack through its REST API, so a team that already encoded its SDLC in Archon can schedule and gate it with kora. Ideas kora borrows from Archon are listed in `knowledge/research/agentic-pipeline-orchestrators/archon.md` (error-class-only retry decisions, `outcome_field`, attention forms readable by the next node, `none_failed_min_one_success` trigger rule, `--dry-run` without providers, declared-only skills and MCP per node, installable workflow packs).

Secondary contrasts:

- **vs vendor cloud sessions** (Claude Code Routines, Codex automations, Cursor Automations, Jules): one prompt → one session → one PR, one vendor, no DAG, no approvals between stages, no cross-run cost view. kora chains them, gates them, retries the failed stage, attributes cost, and runs on any harness and runtime.
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
5. *Select* — an agent node with a cheap model picks the candidate that beats the baseline by the configured threshold from the fan-out's typed list; mutually exclusive candidates for one query are reduced to one.
6. *Review bundle and PR* — kora attaches the evidence to the PR: facts it recorded itself (diff, baseline vs candidate metrics from the gate runs, gate results, cost of all attempts, provenance) and the agent's narrative (why this candidate), rendered apart. The human's only job is to read it and approve; the approval is bound to the commit and the facts.
7. *Rollout to pre-stable* — a node waits for the deploy signal, then a timer and a metrics check confirm the gain on real load.

**Success criterion** for this graph is deterministic: candidate dev run produces equivalent results and improves the recorded metrics by at least the threshold, confirmed after rollout. Agents propose; command nodes measure; kora decides and records.

**What proves kora against Archon** is not this graph on Docker and git (Archon can approximate that) but the same graph running unchanged on a second runtime and VCS implemented through the protocols (the owner's Arcadia CI and arc), with sub-graph retry, content-bound approvals and attempt lineage intact. That is the MVP acceptance test (see `docs/requirements/functional.md`).

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
- Harness surfaces drift (SDK options, event schemas, protocol versions). kora fixes harness versions per installation, wraps the vendor SDKs and ACP behind a harness adapter protocol, and checks tested version ranges at installation health check.
- The merged Prefect–Dagster entity or Airflow ships a session-level node first. kora's defence: harness-agnostic, VCS-agnostic, runtime-agnostic from day one.
- Human review remains the throughput ceiling (AI-authored PRs wait 4.6× longer for review). kora's answer is to reduce the human touchpoints to one evidence-backed PR approval per target (the review bundle), not to produce more PRs.
- Software factories (Open SWE, Factory.ai) may add user-defined graphs and multi-harness support, collapsing the gap from the other side.
- Archon moves fast (3,600+ commits, ~200 open issues, breaking YAML changes each minor) and could add sub-graph retry, content-bound approvals, cron and remote runtimes before kora's MVP ships. kora's defence is the scheduler-grade layer and enterprise protocols, not the node model.

## Evidence, hypotheses and open risks

What is backed by evidence today:

- **The problem is real for the owner.** The owner personally runs several dozen agent-driven tasks; their team's Airflow-like ETL framework serves ~300 authors per week, ~400 graphs and tens of thousands of scheduled processes; each DWH has ~5 data engineers/analysts developing ETL full time, and there are several dozen such DWHs. The YQL optimisation routine described above is done by hand today.
- **The pipeline shape is proven elsewhere.** Google, Airbnb, Anthropic, Dropbox, Stripe and Uber all built the same deterministic-outer-loop shape bespoke (see `knowledge/research/agentic-pipeline-orchestrators.md`).
- **The gap is narrower than first surveyed.** Archon (verified 2026-10-15) already offers a user-defined YAML DAG of coding-agent sessions on the user's own harness with worktrees, approvals, node retries and PR creation. What remains unserved is the orchestration layer listed above: attempt lineage and sub-graph retry, content-bound approvals, domain-typed artifacts, cron/sensor scheduling, and pluggable runtime, VCS and secrets for enterprise environments.

What is a hypothesis, not yet validated:

- **Hypothesis "external demand": demand beyond the owner's organisation.** Other platform teams want an organisation-scale orchestration layer with enterprise protocols enough to adopt a new control plane, rather than running Archon per repository. Test: public pilots after MVP; count organisations (not individuals) that implement a protocol or run more than one team on one kora instance. Archon's ~23.7k stars validate the category; they do not validate the platform layer.
- **Hypothesis "multi-harness value": mixing harnesses per node has value.** Test: in the pilot, measure how often a graph uses more than one harness and whether it changes outcomes or cost.
- **Hypothesis "own scheduler is necessary".** The alternative is an existing engine (Temporal, Kestra, Airflow, CI) plus a session adapter: a whole coding session as one activity/task, with the engine owning retries, waits and approvals. This has not been tried. The owner chose to build the control plane because the enterprise runtime target (Arcadia CI / Sandbox / Tasklets) and VCS (arc) are not reachable from those engines, and because artifact typing, sub-graph retry with attempt lineage and evidence-backed approvals cut across engine primitives. Test: before MVP freeze, assess kora's requirements against existing engines with a minimal adapter and list the limitations an adapter cannot remove. **Result (desk analysis, 2026-10-15, see `knowledge/analysis/own-scheduler-vs-existing-engine/verdict.md`):** Kestra and Airflow fail (no attempt object, retries conflict with `previous_attempt`/per-attempt artifacts, approvals cannot be content-bound, IR-first design conflicts with their plugin surface); Temporal is viable but does not remove kora's own state model, adds a second source of truth and has no production standalone mode. The hypothesis is partly confirmed: an own scheduler is not strictly necessary, but no engine removes the differentiating state model. Recommendation of the analysis: keep the own control plane for MVP; design run state so a Temporal-backed execution backend stays possible; ship Airflow and Kestra integrations, not cores. Owner decision (2026-10-15): own control plane for MVP; run state modelled as interpretable data with explicit attempt objects and waits as rows, so that a Temporal-backed execution backend remains possible later; Airflow and Kestra as integration targets.
- **Hypothesis "outer-loop value": the value is in the outer loop, not in candidate generation.** Harnesses will keep absorbing single-session parallelism (dynamic workflows, Managed Agents, Cursor subagents). Test: pilot metrics below.

Pilot metrics (to be collected on 20–30 real targets before the first public release): accepted useful changes per target; human minutes per accepted change; full cost per accepted change including failed attempts and fan-out losers; maintenance effort of the graph and the platform; comparison against the same work done by hand or via a vendor scheduled session.
