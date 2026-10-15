# kora MVP design — HTTP API and the four thin clients

Status: design draft for owner review (2026-10-15). Scope: FR-0 (AI-first parity), FR-7.2–7.3, FR-11.1, FR-12.2, NFR-0.3, NFR-4.4, NFR-11.3. The HTTP API is the only implementation of every operation; the CLI, the MCP server, the harness skills and the minimal approvals page are derived clients. Owner decisions on design choices are referenced as **DD-n** ("design decision n") and recorded in [design-decisions.md](design-decisions.md).

The MCP server and the skills exist for the **user's own coding-agent session**, so that an agent replaces the manual actions a human would take in the CLI or the page: author, compile, deploy, trigger, inspect, approve, retry, cancel. Agent **nodes** running inside a kora graph have no access to kora's MCP server or API (DD-5, DD-16); the IR gives them only the MCP servers their resolved config lists.

## 1. Conventions

- Base path `/api/v1`; JSON bodies; ids are prefixed ULIDs (`run_…`, `node_…`, `att_…`, `art_…`, `apr_…`, `gv_…`).
- **Authentication** (NFR-4.4): `Authorization: Bearer <token>`. MVP has one static namespace-scoped token per deployment; the MCP server and the approvals page present the user's token and therefore have exactly the user's permissions. The authentication protocol in [protocols.md](protocols.md) replaces the token check without changing routes.
- **Namespace scoping**: every resource path starts with `/namespaces/{ns}`; a token's scope lists the namespaces it may read and write.
- **Idempotency**: every `POST` that creates or mutates accepts `Idempotency-Key`; the server stores `(token, key) → response` for 24 h and replays it. Triggers without a key get a derived one (data-model.md section 3). `PUT`/`DELETE` are idempotent by definition.
- **Pagination**: `?limit=&cursor=`; responses carry `next_cursor`.
- **Long-running reads**: logs and events stream as newline-delimited JSON over `GET …?follow=true` (chunked); no WebSocket in MVP.
- **Versioning** (NFR-11.3): additive changes within `v1`; the OpenAPI document is published at `/api/v1/openapi.json` and is the source from which CLI and MCP tool schemas are generated (section 5).

### Structured errors (FR-0.4)

```json
{ "error": { "code": "IR003", "message": "output 'metrics' of node 'baseline' (MetricsReport) is not assignable to input 'run' of 'review' (CommandResult)",
             "location": { "yaml_path": "graph.nodes.review.inputs.run", "file": "graphs/yql.py", "line": 19 },
             "retryable": false, "details": { "expected": "CommandResult", "actual": "dwh_core/MetricsReport" }, "request_id": "req_01J…" } }
```

Code families: `IRnnn` (IR validation), `DSLnnn` (compile), `APInnn` (request errors: `API001` not found, `API002` conflict, `API003` validation, `API004` idempotency replay mismatch, `API005` forbidden scope, `API006` approval stale, `API007` webhook authentication failed), `RTnnn` (runtime and protocol errors surfaced on attempts). The HTTP status is derived from the code; clients branch on `code`, never on prose.

## 2. Resources and operations

| operation | method and path | notes |
|---|---|---|
| compile DSL | `POST /namespaces/{ns}/compile` | body: source files; returns IR + structured errors; stateless |
| validate IR | `POST /namespaces/{ns}/validate` | body: IR; returns errors and warnings with YAML paths |
| dry run | `POST /namespaces/{ns}/plan` | IR + params → materialised execution plan with fake runtime (NFR-10.1) |
| deploy graph | `POST /namespaces/{ns}/graphs/{graph}/versions` | registers a new version; returns `gv_…`; same digest → same version (idempotent) |
| list/get graphs, versions | `GET /namespaces/{ns}/graphs[/{graph}[/versions/{gv}]]` | version body includes the IR |
| trigger run | `POST /namespaces/{ns}/graphs/{graph}/runs` | body: `params`, optional `graph_version`; returns `run_…` |
| webhook | `POST /hooks/{ns}/{graph}/{trigger_name}` | authenticated per the trigger's `auth`: HMAC body signature in the Standard Webhooks shape, or a bearer token; the secret is resolved through the secrets provider (DD-17); delivery id as idempotency key |
| list/get runs | `GET /namespaces/{ns}/runs?graph=&status=` and `GET /namespaces/{ns}/runs/{run}` | run view includes node instances, totals, timeline cursor |
| run timeline | `GET /namespaces/{ns}/runs/{run}/events?follow=` | the `event` table |
| node, attempts | `GET …/runs/{run}/nodes/{node_path}` and `…/attempts[/{att}]` | `node_path` as in data-model.md |
| logs | `GET …/attempts/{att}/logs?follow=` | normalised session events; `?raw=true` streams the opaque transcript |
| artifacts | `GET …/runs/{run}/artifacts?node=&output=&include_superseded=` and `GET …/artifacts/{art}[/payload]` | lineage via `GET …/artifacts/{art}/lineage?direction=upstream|downstream` |
| cost | `GET …/runs/{run}/cost` | per node, per attempt, per model, completeness; fan-out aggregated |
| retry node | `POST …/runs/{run}/nodes/{node_path}/retry` | body: `{strategy?, use_latest_graph_version?: false}`; FR-6.8 |
| cancel | `POST …/runs/{run}/cancel` and `…/nodes/{node_path}/cancel` | body: `{grace: "60s"}` |
| approvals | `GET /namespaces/{ns}/approvals?status=pending`, `GET …/approvals/{apr}`, `POST …/approvals/{apr}/respond` | the request view carries the form schema, the context artifact, the bound SHA and digests and `attempt_cost` (aggregated cost of all attempts, attached by kora, DD-8); respond body: `{response: {...form...}, commit_sha, digests}`; FR-7.2a check |
| presets | `GET/PUT/DELETE /namespaces/{ns}/presets/{name}` | versioned bodies |
| artifact types | `GET/PUT /namespaces/{ns}/artifact-types/{name}` | FR-4.3 registry |
| namespaces | `GET/PUT /namespaces/{ns}` | policy JSON: concurrency limits, default runtime, default retention |
| schedules | `GET /namespaces/{ns}/graphs/{graph}/triggers`, `POST …/triggers/{name}/pause|resume` | cron, webhook and sensor bindings |
| MCP over HTTP | `POST /mcp` | the same tool set as `kora mcp` (section 4), served by the API process (DD-18) |
| authoring help | `GET /reference/dsl`, `GET /reference/ir-schema`, `GET /namespaces/{ns}/presets` | machine-readable docs for agents (FR-0.1) |
| health | `GET /healthz`, `GET /readyz`, `GET /metrics` | NFR-7.3 |

Every mutating operation records an `event` row with `actor` taken from the token.

## 3. CLI (FR-12.2)

The CLI is generated from the OpenAPI document plus hand-written ergonomics for local files. Mapping:

| command | API call |
|---|---|
| `kora compile graphs/ -o build/` | local compile (same library the API uses); `--remote` posts to `/compile` |
| `kora validate build/yql.ir.yaml` | `/validate` |
| `kora plan build/yql.ir.yaml --param k=v` | `/plan` |
| `kora deploy build/yql.ir.yaml` | `POST /graphs/{graph}/versions` |
| `kora trigger yql_optimization --param k=v [--idempotency-key …]` | `POST /graphs/{graph}/runs` |
| `kora runs [--graph …] [--status …]`, `kora run <run>` | `GET /runs`, `GET /runs/{run}` |
| `kora logs <run> <node_path> [--follow] [--raw]` | `GET …/attempts/{current}/logs` |
| `kora artifacts <run> [--node …]`, `kora artifact <art> [--payload]` | artifact routes |
| `kora cost <run>` | `GET …/cost` |
| `kora approvals`, `kora approve <apr> --input decision=approve`, `kora reject <apr> --input comments=…` | `/approvals` routes; the CLI fetches the request and submits the bound SHA and digests it shows |
| `kora retry <run> <node_path> [--strategy …]`, `kora cancel <run> [<node_path>]` | retry/cancel routes |
| `kora presets …`, `kora namespaces …`, `kora schedules …` | registry routes |
| `kora standalone`, `kora db upgrade` | local operations, not API |

Output: human table by default, `--json` returns the API response verbatim so agents and scripts share the schema.

## 4. MCP server (FR-0.1)

The MCP server exposes **one tool per API operation**, generated from the OpenAPI document, in two forms (DD-18): `kora mcp --url … --token …`, a stdio process for a local coding-agent session that talks to the API with the user's token, and the HTTP MCP endpoint `/mcp` on the API server for remote clients. Both are the same code and the same tool list. Tool names, inputs and outputs:

| tool | input (JSON Schema summary) | output |
|---|---|---|
| `kora_compile` | `{files: {path: source}}` | `{ir, errors[], warnings[]}` |
| `kora_validate` | `{ir}` | `{errors[], warnings[]}` |
| `kora_plan` | `{ir, params}` | `{plan: [{node_path, kind, inputs}], warnings[]}` |
| `kora_deploy` | `{graph, ir}` | `{graph_version_id, number}` |
| `kora_list_graphs`, `kora_get_graph` | `{graph?}` | graph and versions |
| `kora_trigger` | `{graph, params, graph_version?, idempotency_key?}` | `{run_id, status}` |
| `kora_list_runs`, `kora_get_run` | `{graph?, status?, limit?}` / `{run_id}` | run summaries / run with nodes |
| `kora_get_node`, `kora_list_attempts` | `{run_id, node_path}` | node instance, attempts |
| `kora_get_logs` | `{run_id, node_path, attempt?, tail?: 200, raw?: false}` | `{events[], next_cursor}` (no follow; agents poll) |
| `kora_list_artifacts`, `kora_get_artifact` | `{run_id, node?, include_superseded?}` / `{artifact_id, payload?: true}` | artifact metadata, payload when small, storage ref otherwise |
| `kora_get_lineage` | `{artifact_id, direction}` | lineage rows |
| `kora_get_cost` | `{run_id}` | cost tree with completeness |
| `kora_list_approvals`, `kora_get_approval` | `{status?}` / `{approval_id}` | request with form schema, context artifact, bound SHA and digests, `attempt_cost` |
| `kora_respond_approval` | `{approval_id, response, commit_sha, digests}` | `{status}` or `API006` |
| `kora_retry_node`, `kora_cancel` | `{run_id, node_path, strategy?}` / `{run_id, node_path?, grace?}` | updated status |
| `kora_list_presets`, `kora_put_preset`, `kora_list_artifact_types`, `kora_put_artifact_type` | registry bodies | registry rows |
| `kora_get_namespace`, `kora_put_namespace` | policy JSON | namespace |
| `kora_reference` | `{topic: dsl \| ir_schema \| error_codes \| artifact_types}` | documentation text and schemas |

Tool output is the API JSON with the same error object on failure, returned as a tool error with `isError: true` so the agent sees the `code`. The server adds nothing that the API does not do (no local state, no prompts, no separate permission model): every call is made with the user's token and is allowed or refused exactly as the user's own CLI call would be (DD-5, DD-16).

## 5. Harness skills (FR-0.2)

kora ships one skill package per harness, generated from the same reference material and kept in the repository under `skills/`:

- **Claude Code**: a plugin with `skills/kora-author/SKILL.md`, `skills/kora-operate/SKILL.md` and an `.mcp.json` pointing at `kora mcp`.
- **Codex**: the same two documents as `AGENTS.md` sections plus the MCP server block for `config.toml`.

What the skills teach, in order: (1) the DSL in [dsl.md](dsl.md) by example, including presets and artifact types; (2) the authoring loop: write Python, `kora_compile`, fix `DSLnnn`/`IRnnn` errors by `location`, `kora_plan`, `kora_deploy`; (3) the operating loop: `kora_trigger`, poll `kora_get_run`, read artifacts and cost, answer approvals only when the user asks for it; (4) the error object and artifact schemas; (5) rules: never hand-edit `*.ir.yaml`, never put secret values in a graph, always pass `idempotency_key` when retrying a trigger. The skill text is derived from `kora_reference`, so both stay in sync. The skills are installed in the user's own session (plugin or `AGENTS.md`), never into the sessions kora runs as nodes.

## 6. Minimal approvals page (FR-7.3)

A single server-rendered page at `/approvals`. Login (DD-19): the user pastes the API token into a form at `/login`; the page keeps it in an HTTP-only cookie and every action calls the API with that token, so the approver recorded on the request is the token's owner. The page lists pending approval requests for the token's namespaces, each with the context artifact rendered as JSON (a `ReviewBundle` gets a plain table of baseline vs candidate metrics and gate results), the aggregated attempt cost kora attached, the form rendered from the JSON Schema, the bound commit SHA and digests shown read-only, and a submit button that calls `POST …/approvals/{apr}/respond`. A stale response shows the `API006` message and reloads the request. No other page exists in MVP.

## 7. Parity checklist (FR-0.3, release gate)

The checklist is executed by a CI job on every release; a failing line blocks the release.

1. Every operation in the OpenAPI document has a CLI command (generated table in `docs/reference/cli.md` diffed against the spec).
2. Every operation has an MCP tool whose input schema equals the operation's request schema and whose output schema equals the response schema (generated, compared by digest).
3. Every operation appears in the skill reference (`kora_reference` topic index) with at least one example.
4. The approvals page performs `list`, `get` and `respond` through the public API only (no server-side shortcut; verified by routing all page handlers through the API client).
5. Every error code in the registry has a documented recovery hint and appears in the skill's error table.
6. A conformance test drives the canonical graph end to end three times, via CLI, via MCP tools from a fake agent, and via raw HTTP, and compares the resulting `event` timelines modulo actor.
7. No operation requires interactive input: CLI prompts are disabled under `--json`; `kora approve` fails with a structured error when required form fields are missing instead of asking.

## 8. Rate and size limits

Request bodies up to 4 MB (an IR with 1,000 nodes fits); artifact payloads above 1 MB are served by `storage_ref` redirect rather than inline; log streams page at 1,000 events. There are no per-token rate limits in MVP (DD-6).
