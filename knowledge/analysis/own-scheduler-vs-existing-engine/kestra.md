# Own scheduler vs existing engine: kora as a thin adapter on Kestra 2.0

Status: desk analysis, 2026-10-15, testing the hypothesis "own scheduler is necessary" (`positioning.md`) against Kestra 2.0 OSS; the combined verdict is in `verdict.md`. No code. Sources: kora FR/NFR v0.3, `knowledge/research/agentic-pipeline-orchestrators/*`, Kestra docs and source fetched 2026-10-15 (`[K-x]` keys below).

## Adapter model under test

`AgentSessionTask` = Java plugin task (`extends Task implements RunnableTask<Output>`) or, cheaper, a stock `Commands` task running a kora Python runner image via a task runner; the runner streams harness JSONL, enforces `max_events`/budget, emits outputs (`::{"outputs":…}::`) and per-attempt token metrics (`::{"metrics":…}::`). Command node = `Commands`; sub-graph = `Subflow` + `retry`; approval = `Pause` + `onResume`; sensors = polling triggers / `WaitFor`; fan-out = `Loop`/`Parallel`/`Dag`; artifacts = outputs + internal storage + KV; kora DSL → Kestra YAML; custom runtime = custom `TaskRunner`.

Sources: [K-retry] kestra.io/docs/workflow-components/retries · [K-pause] kestra.io/plugins/core/flow/io.kestra.plugin.core.flow.pause · [K-sub] kestra.io/docs/workflow-components/subflows · [K-trig] kestra.io/docs/workflow-components/triggers · [K-conc] kestra.io/docs/workflow-components/concurrency · [K-flow] kestra.io/docs/workflow-components/tasks/flowable-tasks · [K-tr] kestra.io/docs/task-runners/types · [K-dock] kestra.io/plugins/core/task-runners/io.kestra.plugin.scripts.runner.docker.docker · [K-trj] github.com/kestra-io/kestra/blob/develop/core/src/main/java/io/kestra/core/models/tasks/runners/TaskRunner.java · [K-run] same tree, `models/executions/TaskRun.java`, `TaskRunAttempt.java` · [K-plug] kestra.io/docs/plugin-developer-guide/task · [K-oss] kestra.io/docs/oss-vs-paid · [K-wg] kestra.io/docs/enterprise/scalability/worker-group · [K-sec] kestra.io/docs/concepts/secret · [K-mask] kestra.io/docs/best-practices/secrets-management · [K-arch] kestra.io/docs/architecture · [K-live] kestra.io/docs/administrator-guide/servers · [K-otel] kestra.io/docs/administrator-guide/open-telemetry · [K-audit] kestra.io/docs/enterprise/governance/audit-logs · [K-py] kestra.io/docs/api-reference/kestra-sdk/python-sdk · [K-kv] kestra.io/docs/concepts/kv-store · [K-nsf] kestra.io/docs/concepts/namespace-files · [K-replay] kestra.io/docs/concepts/replay · [K-taskrun] kestra.io/docs/workflow-components/tasks/taskruns · [K-out] kestra.io/docs/workflow-components/outputs · [K-cache] kestra.io/docs/workflow-components/task-cache · [K-rel] kestra.io/blogs/release-2-0 · [K-perf] kestra.io/blogs/performance-improvements-2-0 · [K-size] kestra.io/docs/performance/sizing-and-scaling-infrastructure · [K-inst] kestra.io/docs/how-to-guides/selected-plugin-installation · [K-pd] kestra.io/docs/workflow-components/plugin-defaults · [K-err] kestra.io/docs/workflow-components/errors · [K-rev] kestra.io/docs/concepts/revision

## Classification

**native** = Kestra does it; **adapter S/M/L** = kora code (`J` = Java plugin required); **blocked** = needs kora to own scheduler/state.

| Req | Class | Notes / source |
|---|---|---|
| FR-1.1 Python `>>` DSL | adapter M | No code-first builder; `kestrapy` POSTs YAML strings [K-py]. kora writes the compiler. |
| FR-1.2 Versioned YAML IR + schema | adapter M | Kestra YAML is a second, engine-specific IR: no root `version` (server revisions [K-rev]), per-plugin schema. kora IR stays canonical and lowers to it. |
| FR-1.3 Graph / policy / secret-ref sections | adapter S | Kestra mixes topology and config; `secret()` refs only [K-sec]. |
| FR-1.4 compile / validate | adapter S | Edge-type checks are kora-only. |
| FR-1.5 UI editor on same IR | blocked | Kestra editor (OSS no-code [K-rel]) edits the generated YAML; not round-trippable to kora IR. |
| FR-1.6 Trigger rules | adapter M | Only default success chaining, `allowFailure`, `errors`, `finally`, `Dag.dependsOn`, `If` [K-flow], [K-err]; `one_success`/`none_failed`/`all_skipped` lowered to conditions. |
| FR-1.7 Run pinned to IR version | native | Executions run a revision; `?revision=`; draft/publish [K-rev], [K-rel]. |
| FR-2.1 Agent node | adapter L (J opt.) | `plugin-ai AIAgent` is a LangChain4j loop, not a harness. Java task or Python runner image. |
| FR-2.2 Approval gate | native | `Pause` + typed `onResume`, expiry `RESUME/WARN/CANCEL/FAIL`, `resumed.by` [K-pause]; Executor-side, no worker [K-arch]. |
| FR-2.3 Sensor node | native / S | `WaitFor`/`LoopUntil` with `checkFrequency`, `maxDuration` [K-flow]; polling triggers [K-trig]; probes via HTTP task. |
| FR-2.4 Timer | native | `Pause pauseDuration`, `Sleep` [K-pause]. |
| FR-2.5 Branch | native | `If`/`Switch` [K-flow]. |
| FR-2.6 Fan-out / fan-in | native | `Loop values`, `concurrencyLimit`, `transmitFailed:false`, `loopOutputs()` [K-flow]. |
| FR-2.7 Sub-graph unit retry | native / partial | `Subflow`+`retry`: "each retry creates a new execution" [K-retry]; `maxDuration` spans attempts, budget does not; superseded artifacts unmarked. |
| FR-2.7a Iteration, `previous_attempt` | **blocked** | Retry re-renders the same inputs; "all child task runs from the previous attempt are discarded" [K-retry]; `Fail` reason not forwarded. Emulation = `LoopUntil` + KV + kora state machine (L, not thin). Attempt-invariant reuse ≈ `taskCache` [K-cache]. |
| FR-2.8 Command node | native | `Commands`, `taskRunner`, `outputFiles` [K-dock]. |
| FR-3.1 Config vocabulary | adapter M | Task properties, schema-validated. |
| FR-3.2 Preset layering | adapter S | `pluginDefaults` removed in 2.0; Policies are EE [K-pd], [K-rel]. kora compiler materialises merges. |
| FR-3.3 Per-harness mapping | adapter M | In task/runner. |
| FR-3.4 `trust_repo_config` | adapter S | Runner flag. |
| FR-4.1 Typed artifacts, lineage | adapter M | Outputs are untyped maps [K-out]; producer/lineage written by kora into payload. |
| FR-4.2 Built-ins, `ReviewBundle` | adapter M | Conventions; cost aggregation reads child executions via API. |
| FR-4.3 Custom types | adapter S | Validated in runner. |
| FR-4.4 Storage protocol | native | Internal storage, `kestra://` URIs [K-arch], [K-out]. |
| FR-4.5 Per-attempt artifacts | **blocked** | `TaskRun.outputs` is one map (deprecated field); `TaskRunAttempt` = `state, workerId, logFile` [K-run]. Logs/metrics per attempt, outputs not. |
| FR-4.6 Scratch state | adapter S | KV store [K-kv]. |
| FR-5.1 Isolated workspace | adapter S | Clone inside container; or `WorkingDirectory`+`git.Clone` [K-flow]. |
| FR-5.2 VCS protocol (git, arc) | adapter M | In Python runner; Kestra-independent. |
| FR-5.3 VCS per graph/node | adapter S | Compiler config. |
| FR-6.1 Control plane, restart resumes | native / partial | Stateless roles over queue+repo [K-arch]; paused/queued survive, running tasks do not (6.1a). |
| FR-6.1a At-most-once, adopt | **blocked** | `run()` is run-to-completion; worker loss ⇒ jobs "moved to another worker" = re-run [K-live], [K-trj]. No idempotent submit, no lookup, no adoption. |
| FR-6.1b Side-effect boundary | adapter S | Recording only; "at most once" half depends on 6.1a. |
| FR-6.2 Triggers | native | Schedule, Flow `dependsOn`, Webhook `key`, Polling, Realtime, API [K-trig]. |
| FR-6.3 Retry with `retry_on` | **blocked** | Backoff shapes native; retry is unconditional on error class [K-retry]. |
| FR-6.4 `fresh`/`resume`/`fresh_with_feedback` | **blocked** | Needs attempt N artifacts as N+1 inputs (2.7a). Only `fresh`. |
| FR-6.5 Timeouts | native | Task `timeout`, `retry.maxDuration`, wait limits; run-level: S. |
| FR-6.6 `max_events` | adapter M | Counted in runner; error class cannot steer retry. |
| FR-6.7 Cancel with grace | native | `kill()`, Docker `killGracePeriod` [K-dock], [K-trj]. |
| FR-6.8 Manual re-run + downstream | native | Restart / Replay from task, upstream reused, downstream re-run [K-replay]. |
| FR-6.8a Downstream invalidation | partial / M | In-run retries precede downstream; replay makes a new execution, old `Pause` must be killed by kora; no artifact-version binding. |
| FR-6.9 Period quotas | adapter M | KV counters + `Fail`. |
| FR-6.10 Concurrency | native / partial | Flow `concurrency.limit` + `QUEUE/CANCEL/FAIL`, namespace/tenant limits [K-conc]; per-runtime = worker threads or EE Worker Groups [K-wg]. |
| FR-7.1 Channel protocol | adapter M | Only `Pause`; channels = pollers calling `/executions/{id}/resume`; identity = `resumed.by` (basic auth in OSS). |
| FR-7.2 CLI/API + VCS channel | adapter M | Resume native; PR-state poller maps comments → `onResume`. |
| FR-7.2a SHA-bound, auto re-request | **blocked** | `Pause` is resume-or-kill; no external invalidate. SHA check after resume only (S). |
| FR-7.3 Approvals page | native | Executions UI form [K-pause]. |
| FR-7.4 Chat channels | adapter M | As 7.1. |
| FR-7.5 Gate state as data | native | Typed inputs. |
| FR-8.1 Runtime protocol + capability flags | **blocked** | Contract is `run(RunContext, TaskCommands, filesToDownload)` + `kill()` [K-trj]; no status/adopt/heartbeat/flags. |
| FR-8.2 Local + Docker | native | OSS runners [K-tr], [K-dock]. |
| FR-8.3 Sandbox, K8s | EE / adapter L (J) | Kubernetes, AWS/Azure/GCP Batch, Cloud Run, VMs are EE [K-tr]; E2B/Daytona = custom Java runner. |
| FR-8.4 Runtime per node | native | `taskRunner` per task. |
| FR-8.5 Secrets to runtime | adapter S | `env: {{ secret() }}`. |
| FR-8.6 Network policy | adapter S | Docker `networkMode` only; warnings in kora compiler. |
| FR-8.7 Arcadia / Sandbox runtime | adapter L (J) | `TaskRunner` subclass (submit, poll, download, `onKill`), jar in `/app/plugins` [K-trj], [K-inst]; dedicated routing = EE Worker Groups. |
| FR-9.1 Tokens per model per attempt | adapter M | Metrics are per attempt (`Counter.of("tokens.input", n, tags)`) [K-taskrun], [K-plug]; completeness in output. |
| FR-9.2 Price table | adapter S | Runner. |
| FR-9.3 Billing class | adapter S | Output field. |
| FR-9.4 Hard/soft limits | adapter M | Runner-enforced; no native budget primitive. |
| FR-9.5 Cost views | adapter M | Per-attempt metrics shown; cross-attempt/fan-out aggregation = custom dashboard/CLI. |
| FR-9.6 Reconciliation | adapter M | External. |
| FR-10.1 Secrets protocol | native / EE | OSS: `SECRET_*` env vars; Vault/AWS/Azure/own store EE [K-sec]. |
| FR-10.2 Redaction | partial | Full-match masking; obfuscation EE/Cloud; outputs clear text [K-mask], [K-out]. Runner redacts. |
| FR-11.1 Status/logs CLI+API | native | API, UI, SDKs. |
| FR-11.2 Transcript per attempt | adapter S | `outputFiles` with attempt-tagged names. |
| FR-11.3 OTel GenAI, Langfuse, Datadog | adapter M | OTLP traces/metrics native; pass `{{ trace.parent }}` as `TRACEPARENT` [K-otel]; log export EE. |
| FR-11.4 Audit log | EE / adapter | Audit logs EE [K-audit]; linkage via output conventions. |
| FR-11.5 UI, retry diff | native / blocked | UI native; retry diff needs 4.5. |
| FR-12.1 Components | native (JVM) | Kestra roles + Postgres/MySQL [K-arch]. |
| FR-12.2 CLI | adapter S | Wraps `kestrapy`. |
| FR-12.3 compose / standalone | native / partial | `kestra server standalone` + Postgres; no SQLite. |
| FR-12.4 Namespaces, single token | native / partial | Namespaces, basic auth OSS; API tokens, RBAC, namespace secrets, quotas EE [K-oss]. |
| FR-12.5 Versioned registration | native | Revisions [K-rev]. |
| NFR-1.1 Hundreds of sessions, 10k runs/month | partial | Orchestration fine (4,000 exec/min simple [K-perf], 300–400 complex [K-size]); each agent task pins a worker thread for hours → many workers or EE groups. |
| NFR-1.2 Stateless plane, HA | native / EE | HA queues EE [K-oss]. |
| NFR-1.3 Waits hold no capacity | native | Executor-side `Pause`/`Loop`. |
| NFR-1.4 Wide/nested fan-out | native | `Loop`, nested Subflows. |
| NFR-2.1 No double start | **blocked** | Worker loss resubmits (duplicate side effects); worker restarts kill long sessions after grace [K-live]. |
| NFR-2.2 Idempotent triggers | partial | Webhook dedupe undocumented → S. |
| NFR-2.3 Heartbeat / stalled | native / adapter | Server liveness 3 s/45 s [K-live]; agent stall detected in runner. |
| NFR-2.4 Infra vs agent failure | partial | `RESUBMITTED` on worker loss; retry cannot distinguish classes (6.3). |
| NFR-2.5 Bounded, durable waits | native | `Pause` behaviour, `WaitFor.maxDuration`. |
| NFR-6.1 Canonical IR, UI-ready | adapter M | Two IRs; Kestra YAML not round-trippable. |
| NFR-6.2 Deterministic compile | adapter S | kora's job. |
| NFR-6.3 IR migrator | adapter S | Plus Kestra breaking changes (`pluginDefaults`, trigger `conditions` removed) [K-pd], [K-trig]. |
| NFR-6.4 Source / generated / secrets | adapter S | UI edits cause drift. |
| NFR-7.1 Single-command start | native (JVM+Postgres) | Heavier than SQLite. |
| NFR-7.2 Migrations | native | Kestra-managed. |
| NFR-7.3 Health, Prometheus | native | Port 8081 (hardened in 2.0), OTLP/Prometheus [K-otel], [K-rel]. |
| NFR-7.4 Live capacity limits | partial | Worker metrics native; thread counts need restart; queue routing EE. |

## Hard blockers

1. **Attempt model.** Retries re-execute with identical inputs; attempt N+1 never sees attempt N's reason, `VcsRef` or review comments; `RETRY_FLOWABLE`/Subflow retry discards or orphans prior child runs; outputs are one map per task run (only state/worker/log are per attempt). Kills FR-2.7a, 4.5, 6.4, 11.5 and the canonical YQL loop.
2. **Unconditional retry.** No `retry_on`/`non_retryable`: `events_exhausted`, budget, infra and validation failures retry alike (FR-6.3, NFR-2.4).
3. **Run-to-completion runtime contract.** `run()`+`kill()` only; worker crash or deploy re-submits tasks; no `submission_key`, no adoption; hour-long sessions die on worker restarts (FR-6.1a, 8.1, NFR-2.1).
4. **Approvals cannot be invalidated externally.** `Pause` is resume-or-kill; SHA-bound re-request on push needs kora-owned gate state (FR-7.2a).
5. **EE gating of the enterprise-adopter surface:** Kubernetes/cloud runners, Worker Groups, Policies, namespace secrets and secret managers, RBAC/SSO, API tokens, audit logs, log export, HA backends [K-oss], [K-tr], [K-wg], [K-audit]. NFR-12.1 ("no enterprise-only protocol") would rest on a paid product.
6. **JVM-only extension surface.** Plugins and task runners are Java/Gradle shadow jars [K-plug]; SDKs post YAML strings [K-py]. Harness/VCS/secrets/cost logic would run inside the task, where it cannot influence scheduling.
7. **No SQLite; Kestra YAML cannot be the IR** (no edge types, lineage, retry strategies). Minimum footprint JVM + Postgres/MySQL; kora keeps its IR plus a lowering step and a non-round-trippable UI.

## What Kestra gives for free

Scheduler; cron/webhook/flow/polling/realtime/MCP triggers; `Pause` with typed `onResume` and expiry behaviours; `Subflow`; `Loop`/`Parallel`/`Dag`/`If`/`Switch`; flow and namespace concurrency limits; revisions with draft/publish; restart/replay; internal storage; KV store; namespace files with git sync; Process/Docker runners with kill and grace; worker liveness and resubmission; OTLP traces/metrics and Prometheus; REST API with Java/Python/JS/Go SDKs; UI; `taskCache` for attempt-invariant steps; Terraform/CI tooling.

## Operational answers

- **What the user operates:** JVM (Kestra image), PostgreSQL/MySQL, Kestra roles (standalone, or Executor/Scheduler/Webserver/Indexer/Worker-Controller + gRPC Workers), Docker on worker hosts, kora's plugin jar in `/app/plugins`, kora's runner image, and a kora side service for VCS polling/approval mapping. kora's own plan is one Python process + SQLite, or Postgres via docker-compose.
- **OSS vs EE for kora:** OSS = namespaces, basic auth, env-var secrets, Process/Docker, JDBC, flow concurrency, OTel, KV, namespace files. EE = RBAC/SSO/multi-tenancy, namespace secrets and secret managers, Worker Groups, Kubernetes/cloud runners, Policies, audit logs, log export, HA queues, API tokens.
- **YAML-first vs DSL → IR:** Kestra YAML is a lowering target, not the IR; edge types, attempt lineage, retry strategies and SHA-bound gates have no YAML slot and would sit in task properties Kestra does not interpret.
- **Arcadia CI / Sandbox:** Java `TaskRunner` subclass that submits `TaskCommands` + uploaded files as a Sandbox task, polls, downloads `filesToDownload`, honours `onKill`; installed as a jar; selected via `taskRunner.type`. Isolating Arcadia workers needs EE Worker Groups.
- **Cost and `max_events` from a plugin:** the JSONL reader (Java task or Python runner) counts events, prices tokens, kills the harness on breach, emits per-attempt `Counter` metrics tagged by model and an `AgentSession` output. Kestra stores metrics per attempt but has no budget primitive and cannot route retry on the resulting error.

## Verdict

Kestra is a good substrate for a *demo* of the YQL graph (pause, subflow retry, fan-out, Docker runner all exist), but kora's differentiators sit in state Kestra owns and does not expose: attempt lineage with `previous_attempt`, conditional retry, at-most-once submission with adoption, SHA-bound approvals, a runtime protocol richer than run+kill. Removing them means a kora state machine inside Kestra (KV + `LoopUntil` + external pollers), in Java, with the enterprise surface behind EE: an orchestrator inside an orchestrator, not a thin adapter. Hypothesis result for Kestra: **the limitation list is long; keep kora's own control plane** and offer Kestra integration (flow ↔ kora trigger/sensor). Reusable ideas: `Pause.onResume` shape, `TaskRunner` file up/down contract, `taskCache` for attempt-invariant nodes, per-attempt metrics.
