# kora MVP design — the YAML intermediate representation (IR)

Status: design draft for owner review (2026-10-15). Scope: the canonical graph representation that `kora compile` emits, `kora validate` checks and the scheduler consumes (FR-1.2–1.7, FR-2, FR-3.2, FR-6.3–6.6, NFR-6). The machine-readable contract is [ir.schema.json](ir.schema.json); worked examples are in [examples/](examples/). Design decisions taken by the owner on the choices the requirements left open are recorded in [design-decisions.md](design-decisions.md) and referenced here as **DD-n** ("design decision n").

## 1. Design rules

1. **The IR is data, not code.** The scheduler never imports user Python; everything it needs is materialised in the file, including resolved per-node configuration (FR-3.2) and expressions for branches and predicates (section 7).
2. **Stable string ids and named references**, never positional edges (FR-1.5). Node ids are unique within a graph document and match `^[a-z][a-z0-9_]{0,63}$`.
3. **Three concerns in three sections** (FR-1.3): `graph` (topology and node definitions), `runtime` (graph-level runtime and policy configuration), `secrets` (references only, never values).
4. **Lenient at the root, strict inside typed objects** (FR-1.5): unknown root keys are ignored with a warning; unknown keys inside node, retry, limits and config objects are errors. `x-` prefixed keys are allowed anywhere for tool metadata.
5. **Deterministic output** (NFR-6.2): keys in schema order, lists in source order, no timestamps; the only metadata is `meta.compiler` and `meta.source_digest`.
6. **Semver** on the root `version` (NFR-6.3): minor bumps add optional fields, major bumps may remove or re-type fields.

## 2. Document layout

```yaml
version: "1.0"
meta:                         # written by the compiler, informational
  compiler: "kora 0.1.0"
  source_digest: "sha256:…"   # digest of the Python sources that produced this file
graph:                        # section (a): topology
  id: yql_optimization        # graph id, stable across versions
  namespace: dwh_core
  description: "…"
  params: { …JSON Schema… }   # run parameters accepted by triggers
  triggers: [ … ]             # section 8
  overlap: allow              # default for every trigger kind; skip | queue are per-graph options (DD-12)
  nodes: { <node_id>: <Node> }
  subgraphs: { <subgraph_id>: <Subgraph> }   # reusable bodies for subgraph and fan_out nodes
runtime:                      # section (b): policy and infrastructure
  runtime: docker             # runtime protocol implementation, by name
  vcs: git
  artifact_storage: local
  approval_channels: { vcs: {…}, cli: {} }
  concurrency: { max_running_nodes: 20 }
  limits: { timeout: "72h", budget_usd: 200 }      # whole run
  stall_after: "15m"          # runtime-level default for agent-loop silence; nodes override in limits (DD-15)
  retention: { transcripts: "90d", artifact_payloads: "180d" }   # graph-level, namespace default applies when absent (DD-4)
  defaults: { … resolved global+graph layer … }     # informational; nodes carry the resolved config
presets: { <preset_id>: { … } }   # snapshot of each preset as used at compile time, for audit and diffing
secrets:                      # section (c): references only
  aliases: { GITHUB_TOKEN: "secret://env/GITHUB_TOKEN" }
```

`presets` and `runtime.defaults` are **informational snapshots**: the scheduler reads only each node's resolved `config` (FR-3.2); they let a reviewer diff a preset change and a UI show provenance.

## 3. Nodes

Every node has the common envelope below; `config` is kind-specific (section 4).

```yaml
nodes:
  implement:
    kind: agent
    description: "Implement one hypothesis on an isolated checkout"
    source: { file: "graphs/yql.py", line: 23 }        # NFR-10.3
    inputs:
      hypothesis: { type: Hypothesis, from: hypotheses, output: result, item: true }
      previous_attempt: { type: PreviousAttempt, from: $previous_attempt }
    outputs:
      change: { type: VcsRef }
      session: { type: AgentSession }                   # implicit for agent nodes, listed for clarity
    depends_on: []                                      # control-only edges (no artifact)
    trigger_rule: all_success
    retry: { … }                                        # section 6
    limits: { timeout: "2h", max_events: 200, budget_usd: 15, stall_after: "20m" }
    attempt_invariant: false                            # FR-2.7a memoisation flag
    independent_of: []                                  # inputs whose new versions do not invalidate this node (FR-6.8a)
    config: { … }
```

**Edges** are the union of `inputs[*].from` and `depends_on`. An input names the producer node and one of its declared outputs; the validator checks that the producer's output type is assignable to the input type (same type name, or a declared subtype in the artifact-type registry, FR-4.3). Reserved sources: `$params` (run parameters), `$previous_attempt` (section 5), `$inputs` inside a subgraph body (the subgraph's declared inputs, by `path`), `$item` inside a fan-out body (the current list element). Inputs declared with `optional: true` may be unresolved when the producer was skipped; others make the node `upstream_failed`/`skipped` according to the trigger rule.

**Outputs** declare artifact types by registry name. Built-in names: `StructuredOutput`, `VcsRef`, `AgentSession`, `CommandResult`, `ReviewBundle`, plus `List[<type>]` for fan-in results and user types registered under `namespace/Name`. A `StructuredOutput` carries its JSON Schema inline under `schema`. There is no node kind for building artifacts (DD-8): packing a `Candidate`, selecting the best candidate or assembling a `ReviewBundle` is done by an ordinary agent node on a cheap model from its typed inputs, or by a command node when the author wants a fixed formula. kora itself attaches the aggregated cost of all attempts to the approval request (section 4.2); the agent never computes cost.

**Trigger rules** (FR-1.6 plus DD-1): `all_success` (default), `all_done`, `one_success`, `one_failed`, `none_failed`, `all_skipped`, and `none_failed_min_one_success` (borrowed from Archon: no upstream failed and at least one succeeded, so a node after an all-skipped branch stays skipped). The enum is closed in IR v1.

## 4. Node kinds and their `config`

| kind | purpose | required config | produces |
|---|---|---|---|
| `agent` | one harness session | `harness`, `prompt`, resolved harness config (section 4.1) | declared outputs + `AgentSession` |
| `command` | deterministic shell gate | `cmd` (argv list), optional `output_file`, `env`, `workspace` | `CommandResult` and, with `output_file`, a `StructuredOutput` |
| `human_input` | approval gate | `channel`, `form`, `timeout`, `on_expiry`, `binds` | `StructuredOutput` (form response) |
| `wait_event` | sensor | `event` or `poll`, `interval`, `timeout`, `on_expiry` | `StructuredOutput` (event payload) |
| `timer` | durable delay | `duration` or `until` | none |
| `branch` | deterministic routing | `cases: [{when, to}]`, `else` | none; it marks non-chosen targets `skipped` |
| `fan_out` | map a list over a subgraph | `over` (list input), `body` (subgraph id), `max_parallel`, `skip_failures` | per-item instances; `fan_in` collects |
| `fan_in` | collect fan-out results | `from_fan_out`, `collect` (output name in the body) | `List[<type>]` |
| `subgraph` | graph as a node | `body` (subgraph id), `inputs` mapping | the body's declared outputs |

### 4.1 Agent node config (resolved, FR-3.1)

```yaml
config:
  harness: claude                     # claude | codex (adapter name); the harness version is fixed by the installation, not the node (DD-14)
  model: claude-opus-5-5
  prompt: "Implement hypothesis {{ inputs.hypothesis.title }} …"   # template over inputs and params
  system_prompt_append: "…"
  skills: [yql-optimizer]             # resolved to content digests at deploy time
  subagents: [ {name: …, description: …, prompt: …, tools: […]} ]
  hooks: {}                           # harness hook config, declared-only
  mcp_servers: { yt: { type: stdio, command: "yt-mcp", args: ["--cluster", "hahn"] } }   # agent nodes never get kora's own MCP server (DD-5)
  allowed_tools: ["Bash(etl *)", "Read", "Edit"]
  disallowed_tools: []
  permission_mode: dont_ask           # kora vocabulary, mapped per harness
  sandbox: workspace_write            # kora vocabulary, mapped per harness
  trust_repo_config: false            # FR-3.4
  network_policy: { egress: ["api.anthropic.com", "yt.internal"] }   # FR-8.6; enforced by the runtime, harness sandbox is a second layer (DD-21)
  workspace: { repo: "…", rev: "{{ inputs.change.commit | default('main') }}" }
  output_schema: { … JSON Schema … }  # for the StructuredOutput output
  env: { ETL_ENV: dev }
  secret_env: { GITHUB_TOKEN: GITHUB_TOKEN }   # env var -> alias in secrets.aliases
  runtime: docker                     # per-node override of runtime.runtime (FR-8.4)
  vcs: git                            # per-node override (FR-5.3)
  consumes_previous_attempt: true     # FR-2.7a
```

`workspace.from_ref` names an input path holding a `VcsRef`; when that artifact is present the checkout uses its commit, otherwise `rev` (this is how `fresh_with_feedback` starts from the previous attempt's branch). `permission_mode` values: `plan`, `accept_edits`, `dont_ask`, `bypass`. `sandbox` values: `read_only`, `workspace_write`, `full_access`. The harness mapping tables in [protocols.md](protocols.md) say which flag each becomes and which fields a harness cannot honour (compile-time warning, FR-3.3). Harness API keys reach the session through the runtime's credential proxy when it declares that capability, otherwise as environment variables (DD-23).

### 4.2 Human-input node config (FR-2.2, FR-7)

```yaml
config:
  channel: vcs                         # name in runtime.approval_channels
  form: { type: object, properties: { decision: {enum: [approve, reject]}, comments: {type: string} }, required: [decision] }
  timeout: "3d"
  on_expiry: fail                      # fail | skip | default_value
  default_value: null
  binds:                               # content binding, FR-7.2a
    vcs_ref: bundle.change             # input path whose commit SHA the approval covers
    gate_digests: [bundle.gates]       # input paths whose artifact digests the approval covers
  on_new_push: re_request              # re_request | rerun_gates
  approve_when: "response.decision == 'approve'"   # CEL expression that makes the node succeed; otherwise it fails with class approval_rejected
```

kora attaches to every approval request the aggregated estimated cost of all attempts that led to the bound artifact (lineage walk over usage records, [data-model.md](data-model.md) section 5), so the approver sees it next to the form without any node computing it (DD-8).

### 4.3 Wait-event node config (FR-2.3)

```yaml
config:
  event: { type: webhook, name: deploy.finished, match: "payload.target == inputs.target.id" }
  # or: poll: { sensor: change_request_status, config: { ref: inputs.change }, until: "status == 'merged'" }
  interval: "5m"
  timeout: "2d"
  on_expiry: fail
```

`poll.sensor` names an implementation of the **sensor protocol** ([protocols.md](protocols.md) section 11, DD-2); kora ships `change_request_status` and `ci_status` (through the VCS protocol) and `http` (GET a URL and expose the JSON body). `until` is a CEL expression over the observation the sensor returns. Adopters add sensors through entry points without changing the IR.

### 4.4 Fan-out, fan-in and subgraph

```yaml
candidates:
  kind: fan_out
  inputs: { items: { type: "List[Hypothesis]", from: hypotheses, output: result } }
  config: { over: items, body: attempt, max_parallel: 3, skip_failures: true,
            bind: { baseline: { from: baseline, output: metrics } } }
collect:
  kind: fan_in
  config: { from_fan_out: candidates, collect: candidate }
  outputs: { candidates: { type: "List[Candidate]" } }
```

A fan-out materialises one **instance** of the body subgraph per list element at run time (NFR-3.3), node ids suffixed `[i]`. `skip_failures: true` collects the successful instances and succeeds when at least one succeeded; `false` fails on the first failed instance. Nested fan-out inside a body is allowed (NFR-1.4).

A `subgraphs` entry has the same shape as `graph` minus `triggers`, plus `inputs` and `outputs` declarations and its own `retry` and `limits`, which apply to the body as a unit (FR-2.7).

## 5. Sub-graph iteration semantics (FR-2.7a)

A loop is a subgraph with `retry.max_attempts > 1`. Rules materialised in the IR:

- A node inside the body can end the attempt with `fail_attempt`: a `branch` case whose `to` is the reserved target `$fail_attempt` with a `reason` expression. The attempt ends `failed` with error class `attempt_failed`.
- Attempt N+1 receives the subgraph's inputs plus `$previous_attempt`, a built-in `PreviousAttempt` artifact: `{attempt: N, reason, error_class, artifacts: {<node_id>.<output>: <artifact ref>}}`. Only nodes with `consumes_previous_attempt: true` receive it; they declare the input explicitly so the dependency is visible.
- Nodes with `attempt_invariant: true` are not re-run in attempt N+1; their attempt-N artifact version is reused and lineage records the reuse.
- `limits` on the subgraph (timeout, budget) span all attempts; `limits` on body nodes apply per attempt. `retry.max_attempts` on the subgraph counts attempts of the unit; body nodes keep their own node-level retries inside each attempt.
- Artifacts of every attempt are retained; the previous attempt's versions are marked `superseded` when a new attempt starts (FR-4.5).
- VCS side effects of failed attempts are never touched (FR-2.7, FR-6.1b).

## 6. Retry policy and strategy (FR-6.3, FR-6.4)

```yaml
retry:
  max_attempts: 3
  initial_interval: "30s"
  backoff_coefficient: 2.0
  max_interval: "10m"
  retry_on: [harness_error, timeout, events_exhausted, stalled, artifact_invalid]
  non_retryable: [budget_exhausted, approval_rejected]
  predicate: "outputs.verdict.approved == false"     # optional extra condition on artifacts
  strategy: fresh_with_feedback                       # agent nodes only: fresh | resume | fresh_with_feedback
```

Error classes (closed enum, used by `retry_on`, `non_retryable` and the data model): `harness_error`, `command_failed`, `timeout`, `events_exhausted`, `budget_exhausted`, `artifact_invalid`, `attempt_failed`, `approval_rejected`, `approval_expired`, `wait_expired`, `stalled`, `infrastructure`, `cancelled`, `config_error`. `infrastructure` and `stalled` never consume **agent** attempts: they are retried under a separate `infrastructure_retry` policy at `runtime` level (NFR-2.4). `config_error` and `cancelled` are never retryable (Archon's "never retry config errors").

`strategy: resume` requires the runtime capability `supports_resume`; when the session handle is gone the attempt falls back to `fresh_with_feedback` and records `strategy_fallback` (FR-6.4).

## 7. Expressions and templates

Branch conditions, retry predicates, `approve_when`, event `match` and sensor `until` are **CEL** (Common Expression Language) expressions (DD-3), evaluated by kora with `cel-python` in the reference implementation and by any conforming CEL implementation in other SDKs. The evaluation environment binds `inputs.<name>` (artifact payloads and summaries), `outputs.<name>`, `params`, `response` (approval form response), `payload` (inbound event) and `observation` (sensor result). Only CEL's standard functions are available; no custom functions and no user code.

Prompt templates (`prompt`, `system_prompt_append`, `cmd` elements) are **Jinja2 in a sandboxed environment** (DD-11): the full template language, the same variables as above, no filesystem loaders, no imports or includes, no access to Python attributes beyond the bound data. Templates are rendered once at attempt start and the rendered text is recorded on the attempt.

## 8. Triggers (FR-6.2)

```yaml
triggers:
  - { kind: manual }
  - { kind: cron, expr: "0 6 * * 1", timezone: Europe/Moscow, catchup: false }
  - { kind: webhook, name: optimize_target, auth: { kind: hmac, secret: WEBHOOK_SECRET }, params_from: payload }
  - { kind: webhook, name: optimize_from_ci, auth: { kind: bearer, secret: CI_TOKEN } }
  - { kind: sensor, poll: { sensor: http, config: { url: "…" }, until: "observation.body.ready == true" }, interval: "15m" }
  - { kind: graph, graph: upstream_graph, on: run_succeeded }
```

Every trigger produces a run with an idempotency key (section 2 of [data-model.md](data-model.md)). Overlap handling is `graph.overlap`: `allow` (default for every trigger kind), `skip` or `queue` (DD-12). Webhook authentication is either an HMAC body signature in the Standard Webhooks shape or a bearer token, chosen per trigger; the secret is a reference into `secrets.aliases` (DD-17).

## 9. Limits (FR-6.5, FR-6.6, FR-9.4)

| field | level | kind | enforced by |
|---|---|---|---|
| `timeout` | node, subgraph, run | hard | scheduler (wall clock) |
| `max_events` | agent node | hard | harness adapter counting JSONL events; class `events_exhausted` |
| `budget_usd` | agent node, subgraph, run | soft | adapter from streamed usage; also passed to the harness where supported; class `budget_exhausted` |
| `budget_headroom_pct` | runtime defaults | – | tolerated overshoot before termination (NFR-8.2) |
| `stall_after` | agent node; runtime default `15m` | hard | worker, from silence in the event stream; class `stalled` (DD-15) |

## 10. Validation rules (`kora validate`)

Schema validation first, then semantic checks, each with a stable code and a YAML path (FR-0.4): `IR001` acyclic graph (including `depends_on`), `IR002` every `from` resolves, `IR003` output/input type assignable, `IR004` fan-out `over` is list-typed and `fan_in.from_fan_out` matches, `IR005` `binds` paths resolve to a `VcsRef` and digest-bearing artifacts, `IR006` `$previous_attempt` only inside a retried subgraph, `IR007` secret aliases resolve and no value-like strings in `env`, `IR008` harness cannot honour a field (warning), `IR009` runtime lacks a requested capability such as `enforces_network_policy` or `supports_resume` (warning), `IR010` branch targets exist and `$fail_attempt` only inside a subgraph, `IR011` CEL expressions parse and type-check against the declared artifact schemas, `IR012` Jinja2 templates parse in the sandbox.

## 11. Worked examples

- [examples/yql-optimization.ir.yaml](examples/yql-optimization.ir.yaml): the canonical graph from `positioning.md` compiled from Appendix A of the FR: target → baseline → hypotheses → fan-out of a retried subgraph (implement → dev run → equivalence → review → branch → pack candidate) → select best (cheap agent) → review bundle (cheap agent) → VCS approval with kora-attached cost → wait for deploy → timer → metrics check.
- [examples/dependency-upgrade.ir.yaml](examples/dependency-upgrade.ir.yaml): a small cron graph: Codex upgrades one dependency, a command node runs the tests, an approval on the CLI channel, then a sensor waits for the merge.

Both validate against the schema.
