# kora MVP design — HTTP API and the four thin clients

Status: design draft for owner review (2026-10-15, revised after the design review, see [review-2026-10-15.md](../reviews/review-2026-10-15.md)). Scope: FR-0 (AI-first parity), FR-7.2–7.3, FR-11.1, FR-11.5, FR-12.2, NFR-0.3, NFR-4.4, NFR-11.3, NFR-13.1. The HTTP API is the only implementation of every operation; the CLI, the MCP server, the harness skills and the web console are derived clients. Owner decisions on design choices are referenced as **DD-n** ("design decision n") and recorded in [design-decisions.md](../reviews/design-decisions-2026-10-15.md).

The MCP server and the skills exist for the **user's own coding-agent session**, so that an agent replaces the manual actions a human would take in the CLI or the page: author, compile, deploy, trigger, inspect, approve, retry, cancel. Agent **nodes** running inside a kora graph have no access to kora's MCP server or API (DD-5, DD-16); the IR gives them only the MCP servers their resolved config lists.

## 1. Conventions

- Base path `/api/v1`; JSON bodies; ids are prefixed ULIDs (`run_…`, `node_…`, `att_…`, `art_…`, `apr_…`, `gv_…`).
- **Authentication** (NFR-4.4, DD-32): `Authorization: Bearer <token>`. **Personal user tokens** are bound to a person and a namespace list and carry exactly two flags: `approve` (may respond to approvals) and `platform` (may publish presets, namespace policies and tool policies); reading transcripts and session segments needs the `transcripts` capability on the namespace entry (design review finding [transcript access, audit and supply chain](../reviews/review-2026-10-15.md#transcripts-audit-supply-chain)). No roles or groups. **Service tokens** belong to workers, are bound to a namespace list enforced at claim (design review finding [namespace-scoped principals](../reviews/review-2026-10-15.md#namespace-scoped-principals)) and may only claim, heartbeat, upload and transition attempts whose lease they hold. Every by-id route joins on `namespace_id` against the token's list. The MCP servers and `kora web` present the user's token and therefore have exactly the user's permissions; the MCP server's default token has no `approve`. The authentication protocol in [protocols.md](protocols.md) replaces the token check without changing routes.
- **Namespace scoping**: every resource path starts with `/namespaces/{ns}`; a token's scope lists the namespaces it may read and write.
- **Idempotency**: every `POST` that creates or mutates accepts `Idempotency-Key`; the server stores `(token, key, request digest) → response` in the `idempotency_record` table for 24 h and replays it from any replica. Triggers without a key get a derived one, a manual trigger a fresh ULID (data-model.md section 3); `params` are validated against the graph version's `params` schema (DD-36). `PUT`/`DELETE` are idempotent by definition.
- **Lease fencing**: every worker write carries `lease_epoch`; a stale epoch is refused with `API008` (scheduler.md section 3).
- **Pagination**: `?limit=&cursor=`; responses carry `next_cursor`.
- **Long-running reads**: logs and events stream as newline-delimited JSON over `GET …?follow=true` (chunked); no WebSocket in MVP.
- **Versioning** (NFR-11.3): additive changes within `v1`; the OpenAPI document is published at `/api/v1/openapi.json`; the web TypeScript client is generated from it, the Python client is hand-written over the same Pydantic models and contract-tested against it, and the parity coverage test (section 7) reads it.

### Structured errors (FR-0.4)

```json
{ "error": { "code": "IR003", "message": "output 'metrics' of node 'baseline' (MetricsReport) is not assignable to input 'run' of 'review' (CommandResult)",
             "location": { "yaml_path": "graph.nodes.review.inputs.run", "file": "graphs/sql.py", "line": 19 },
             "retryable": false, "details": { "expected": "CommandResult", "actual": "dwh_core/MetricsReport" }, "request_id": "req_01J…" } }
```

Code families: `IRnnn` (IR validation), `DSLnnn` (compile), `APInnn` (request errors: `API001` not found, `API002` conflict, `API003` validation, `API004` idempotency replay mismatch, `API005` forbidden (namespace outside the token's list, missing `approve` or `platform` flag, missing `transcripts` capability), `API006` approval stale (the only code for a bound-content mismatch or a request that is no longer `pending`, design review finding [stale-approval and sensor error codes](../reviews/review-2026-10-15.md#stale-approval-and-sensor-codes)), `API007` webhook authentication failed (bad signature, bad bearer, or a Standard Webhooks timestamp outside the tolerance window), `API008` stale lease epoch), `RTnnn` (runtime and protocol errors surfaced on attempts), `POLnnn` (policy denials recorded on attempts). The HTTP status is derived from the code; clients branch on `code`, never on prose.

## 2. Resources and operations

| operation | method and path | notes |
|---|---|---|
| validate IR | `POST /namespaces/{ns}/validate` | body: IR; returns errors and warnings with YAML paths |
| dry run | `POST /namespaces/{ns}/plan` | IR + params → materialised plan with fake runtime (NFR-10.1) |
| deploy graph | `POST /namespaces/{ns}/graphs/{graph}/versions` | registers a new version; returns `gv_…`; same digest → same version (idempotent) |
| list/get graphs, versions | `GET /namespaces/{ns}/graphs[/{graph}[/versions/{gv}]]` | version body includes the IR |
| trigger run | `POST /namespaces/{ns}/graphs/{graph}/runs` | body: `params`, optional `graph_version`; returns `run_…` |
| webhook | `POST /hooks/{ns}/{graph}/{trigger_name}` | authenticated per the trigger's `auth`: HMAC body signature in the Standard Webhooks shape, or a bearer token; the secret is resolved through the secrets provider (DD-17); delivery id as idempotency key |
| list/get runs | `GET /namespaces/{ns}/runs?graph=&status=` and `GET /namespaces/{ns}/runs/{run}` | run view includes node instances, totals, timeline cursor |
| run timeline | `GET /namespaces/{ns}/runs/{run}/events?follow=` | the `event` table |
| node, attempts | `GET …/runs/{run}/nodes/{node_path}` and `…/attempts[/{att}]` | `node_path` as in data-model.md |
| attempt diff | `GET …/runs/{run}/nodes/{node_path}/attempts/{att}/diff?against={att}` | compares two attempts of one node (FR-4.5, FR-11.5); `against` defaults to the previous attempt of the same node instance, else the last attempt of the same node in the previous sub-graph attempt. Built only from kora's own records: strategy and `strategy_fallback`, the `Feedback` passed in, status and error class, `harness_version`, `image_digest`, `price_table_id`, the resolved config digest with the changed config paths when the two attempts ran different graph versions, event counts, usage and cost with completeness, and per declared output the two artifact versions with their digests, a JSON Patch (RFC 6902) between `StructuredOutput` payloads and both `VcsRef`s (branch, commit, diff summary, change request URL); the code diff itself stays on the code host |
| logs | `GET …/attempts/{att}/logs?follow=` | normalised session events read from the uploaded segments, live while the attempt runs (design review finding [live log segments](../reviews/review-2026-10-15.md#live-log-segments)); `?raw=true` streams the opaque transcript |
| artifacts | `GET …/runs/{run}/artifacts?node=&output=&include_superseded=` and `GET …/artifacts/{art}[/payload]` | lineage via `GET …/artifacts/{art}/lineage?direction=upstream\|downstream` |
| cost | `GET …/runs/{run}/cost` and `GET /namespaces/{ns}/cost/daily` | per node, per attempt, per model, completeness; fan-out aggregated; the daily view shows spend against the namespace's daily soft limit and whether admission is blocked (DD-31) |
| retry node | `POST …/runs/{run}/nodes/{node_path}/retry` | body: `{strategy?, use_latest_graph_version?: false}`; FR-6.8; the clear-downstream transaction; refused with `API002` for a `human_input` node failed with `candidate_moved`, naming the node whose attempt produced the bound `VcsRef` (scheduler.md section 5) |
| cancel | `POST …/runs/{run}/cancel` and `…/nodes/{node_path}/cancel` | body: `{grace: "60s"}` |
| approvals | `GET /namespaces/{ns}/approvals?status=pending`, `GET …/approvals/{apr}`, `POST …/approvals/{apr}/respond` | the request view carries the form schema, the context artifact (`ReviewBundle` facts and narrative apart, DD-33), the bound SHA and digests and `run_cost` (DD-8); respond body `{response: {…form…}, commit_sha, digests: {path: digest}}`, validated against the form, requires the `approve` flag, approver recorded as the token owner; `approve_when` false → class `approval_rejected` (DD-36); FR-7.2a check → `API006`, also for a request no longer `pending`, whose response is recorded as an `approval.responded` event with `applied: false` (scheduler.md section 5) |
| presets | `GET/PUT/DELETE /namespaces/{ns}/presets/{name}` | versioned bodies; writes require `platform` (DD-32) |
| artifact types | `GET/PUT /namespaces/{ns}/artifact-types/{name}` | FR-4.3 registry |
| namespaces | `GET/PUT /namespaces/{ns}` | policy JSON: concurrency limits incl. `max_pending_runs`, default runtime, default retention, `tool_policy` (DD-29), `daily_budget_usd` (DD-31), secret prefixes, `approver_allowlist` (DD-35), `require_network_enforcement`, `allow_trust_repo_config`, `http_allowlist`; writes require `platform` (DD-32) |
| schedules | `GET /namespaces/{ns}/graphs/{graph}/triggers`, `POST …/triggers/{name}/pause\|resume` | cron, webhook and sensor bindings |
| MCP over HTTP | `/mcp/authoring`, `/mcp/operating` | the two MCP servers of section 4, served by the API process (DD-18, DD-28) |
| workers | `POST /workers` (register), `POST /workers/{id}/claims` (body `{runtime, max: 1}`, returns one self-contained claim or none, DD-36), `GET /workers` | service principals; `last_seen_at` updates on claims and attempt heartbeats, there is no worker heartbeat route (design review finding [redundant liveness and keys](../reviews/review-2026-10-15.md#redundant-liveness-and-keys)) |
| attempt reporting | `POST /attempts/{att}/heartbeat` → `{desired_state, lease_until}`, `POST /attempts/{att}/segments`, `POST /attempts/{att}/artifacts` (multipart), `POST /attempts/{att}/usage`, `POST /attempts/{att}/transition` | every call carries `lease_epoch`; uploads are chunked and exempt from the 4 MB body limit; the API validates artifacts against their declared schema on upload and promotes `vcs.*` and `permission_denied` facts to event rows (design review finding [one event concept for exporters](../reviews/review-2026-10-15.md#one-event-concept)); an artifact upload and a transition to `finishing` also require result authority (`desired_state = run` and the attempt being its instance's `current_attempt_id`, [data-model.md](data-model.md) section 3): without it the artifact is stored as `superseded` and the transition response carries `desired_state: cancel` instead of the new status, while segments and usage are always accepted under a valid epoch |
| frontend session | `POST /auth/login-links` (Bearer token → one-time link, single-use code valid 60 s, used by `kora open`), `GET /auth/session/{code}` (code → opaque session cookie, redirect to the console), `POST /auth/session` (pasted token → session cookie, fallback), `DELETE /auth/session` | cookie authentication is accepted only on frontend routes, never on Bearer-only API paths (design review finding [server-side session cookie](../reviews/review-2026-10-15.md#server-side-session-cookie)) |
| authoring help | `GET /reference/dsl`, `GET /reference/ir-schema`, `GET /namespaces/{ns}/presets` | machine-readable docs for agents (FR-0.1) |
| health | `GET /healthz`, `GET /readyz`, `GET /metrics` | NFR-7.3 |

Every mutating operation records an `event` row with `actor` taken from the token.

Compiling Python sources is not an API operation: it runs in the author's own process (`kora compile`, or the stdio authoring server's `kora_compile`, sections 3 and 4), and the API accepts only compiled documents, so no control-plane process ever imports graph authors' code (ADR-0002, amended 2026-10-16; [dsl.md](dsl.md) section 2).

Body shapes for the first slice (DD-36): deploy `{graph, ir}` → `{graph_version_id, number, warnings[]}`; run `{params, graph_version?}` → `{run_id, status}`; run view `{run, nodes: [{node_path, kind, status, subgraph_attempt, attempt_count, current_attempt}], totals}`; node view `{instance, attempts[], artifacts[]}`; transition `{to, lease_epoch, handle?, error_class?, error?}` → `{status, desired_state}`.

## 3. CLI (FR-12.2)

The CLI is a Typer application over the hand-written `httpx` client that shares the Pydantic request and response models with the API (design review finding [one shared API client](../reviews/review-2026-10-15.md#one-shared-api-client)). It prints the API error envelope on failure and exits 1 (DD-36). Mapping:

| command | API call |
|---|---|
| `kora compile graphs/ -o build/` | no API call: imports the graph modules in the CLI process with the caller's own privileges and compiles them with the shared compiler library, whose validator is the one behind `/validate` ([dsl.md](dsl.md) section 2); a client-side operation under the parity rule of section 7 |
| `kora validate build/sql.ir.yaml` | `/validate` |
| `kora plan build/sql.ir.yaml --param k=v` | `/plan`: prints the materialised plan |
| `kora deploy build/sql.ir.yaml` | `POST /graphs/{graph}/versions` |
| `kora trigger sql_optimization --param k=v [--idempotency-key …] [--dry-run]` | `POST /graphs/{graph}/runs`; `--dry-run` runs the graph on the fake runtime and fake harness (design review finding [process names and dry-run commands](../reviews/review-2026-10-15.md#process-names-and-dry-run)) |
| `kora graphs [<graph>] [--version <n>]` | `GET /graphs[/{graph}[/versions/{gv}]]`: graphs, their versions, one version with its IR |
| `kora runs [--graph …] [--status …]`, `kora run <run>` | `GET /runs`, `GET /runs/{run}`; columns: run id, graph, version, status, trigger, started, duration, cost estimate; `kora run` adds the node table with node path, kind, status, sub-graph attempt, attempts, cost (DD-36) |
| `kora events <run> [--follow]` | `GET …/runs/{run}/events`: the run timeline |
| `kora node <run> <node_path> [--attempt <att>]` | `GET …/nodes/{node_path}`, `…/attempts[/{att}]`: the instance, its attempts and their artifacts |
| `kora diff <run> <node_path> [--attempt <att>] [--against <att>]` | `GET …/attempts/{att}/diff`; defaults to the current attempt against the one before it |
| `kora logs <run> <node_path> [--follow] [--raw]` | `GET …/attempts/{current}/logs` |
| `kora artifacts <run> [--node …]`, `kora artifact <art> [--payload]` | artifact routes |
| `kora cost <run>` | `GET …/runs/{run}/cost` |
| `kora cost --daily [--namespace <ns>]` | `GET /namespaces/{ns}/cost/daily` |
| `kora approvals`, `kora approve <apr> --input decision=approve`, `kora reject <apr> --input comments=…` | `/approvals` routes; the CLI fetches the request and submits the bound SHA and digests it shows; `kora reject` sets the form's decision field; both need a token with `approve` |
| `kora retry <run> <node_path> [--strategy …]`, `kora cancel <run> [<node_path>]` | retry/cancel routes |
| `kora presets …`, `kora namespaces …`, `kora schedules …`, `kora workers` | registry and worker routes |
| `kora presets pull` | fetches registry presets into `.kora/presets/` so `kora compile` stays offline (design review finding [speculative mechanisms](../reviews/review-2026-10-15.md#speculative-mechanisms)) |
| `kora plugins list`, `kora doctor` | local: installed protocol implementations and their config schemas; installation health incl. harness version ranges (design review finding [harness version health check](../reviews/review-2026-10-15.md#harness-version-health-check)) |
| `kora conformance <protocol> <name>` | local: runs the conformance suite against an implementation (NFR-10.2) |
| `kora standalone`, `kora db upgrade`, `kora db verify-storage` | local operations, not API |

Output: human table by default, `--json` returns the API response verbatim so agents and scripts share the schema.

## 4. MCP servers (FR-0.1, DD-28)

kora ships **two curated MCP servers** with task-shaped tools, not one generated tool per API operation: `kora-authoring` for writing and deploying graphs and `kora-operating` for running and inspecting them. Both run as the stdio process `kora mcp --server authoring|operating --url … --token …` for a local coding-agent session and are mounted on the API at `/mcp/authoring` and `/mcp/operating` (DD-18). The HTTP-mounted authoring server omits `kora_compile`, which would run authors' code inside the API process; a session that uses it compiles with `kora compile` and passes the document to `kora_plan` and `kora_deploy`. Every other tool calls the API with the user's token through the shared client; the servers add no state and no permission model of their own (DD-5, DD-16). Long operations (`kora_plan`, `kora_trigger`, `kora_retry`) are exposed as **MCP Tasks** per the 2026-07 specification, so an agent starts them, receives a task id and polls or awaits completion instead of blocking on a tool call.

| server | tool | what it does (API operations behind it) |
|---|---|---|
| authoring | `kora_reference` | DSL reference, IR schema, error codes, and reads of the registry: presets and artifact types of the namespace, deployed graphs and their versions (`/reference/*`, `GET` presets, artifact types, graphs and versions) |
| authoring | `kora_compile` | compile the graph sources in the MCP server's own process, which is the author's environment, then validate the document through `/validate`, and return IR plus `DSLnnn`/`IRnnn` errors and warnings in one call; stdio server only (local compile, `/validate`) |
| authoring | `kora_plan` (task) | materialised plan on the fake runtime, with unhonoured-field warnings (`/plan`) |
| authoring | `kora_deploy` | register a graph version (`POST /graphs/{graph}/versions`) |
| operating | `kora_trigger` (task) | start a run with params and idempotency key; the task completes when the run reaches a terminal state or a waiting state the caller asked to stop at (`POST /graphs/{graph}/runs`, `GET /runs/{run}`) |
| operating | `kora_runs` | list runs by graph and status, or get one run with its node instances, totals and timeline events (`GET /runs`, `GET /runs/{run}`, `GET /runs/{run}/events`) |
| operating | `kora_inspect` | one node of a run: instance, attempts, tail of normalised events, artifacts with lineage, payload when small, and the diff between two of its attempts (node, attempt, attempt diff, logs, artifact routes) |
| operating | `kora_cost` | cost tree of a run with completeness, or the namespace's daily spend against its limit |
| operating | `kora_approvals` | list pending approvals or get one with form, context artifact, bound SHA and digests, `run_cost` |
| operating | `kora_respond` | respond to an approval with the bound SHA and digests (`API006` on staleness); requires a token with `approve`, which the MCP server's default token lacks (DD-32) |
| operating | `kora_retry` (task) | manual retry of a node (clear downstream) and wait for the node's next terminal state |
| operating | `kora_cancel` | cancel a run or a node with grace |
| operating | `kora_admin` | the `platform`-flag and operator operations: put presets and artifact types, put namespace policy, pause or resume a schedule, list workers; refused with `API005` without `platform` |

Tool output is the API JSON with the same error object on failure, returned as a tool error with `isError: true` so the agent sees the `code`. Thirteen tools in total, under the 15-tool cap; skills are files, not MCP resources (design review finding [skills and MCP overlap](../reviews/review-2026-10-15.md#skills-and-mcp-overlap)).

## 5. Harness skills (FR-0.2)

kora ships one skill package per harness, generated from the same reference material and kept in the repository under `skills/`:

- **Claude Code**: a plugin with `skills/kora-author/SKILL.md`, `skills/kora-operate/SKILL.md` and an `.mcp.json` pointing at the two `kora mcp` servers.
- **Codex**: the same two documents as Codex skills (`skills.config[]`, DD-20) plus the MCP server blocks for `config.toml`.

What the skills teach, in order: (1) the DSL in [dsl.md](dsl.md) by example, including presets and artifact types; (2) the authoring loop: write Python, `kora_compile`, fix `DSLnnn`/`IRnnn` errors by `location`, `kora_plan`, `kora_deploy`; (3) the operating loop: `kora_trigger`, `kora_runs`, `kora_inspect`, `kora_cost`, answer approvals only when the user asks for it and only with a token that carries `approve`; (4) the error object and artifact schemas; (5) rules: never hand-edit `*.ir.yaml`, never put secret values in a graph, always pass `idempotency_key` when retrying a trigger. The skill text is derived from the same reference material as `kora_reference`, so both stay in sync; skills are files in each harness's own format and nothing else (design review finding [skills and MCP overlap](../reviews/review-2026-10-15.md#skills-and-mcp-overlap)). They are installed in the user's own session, never into the sessions kora runs as nodes.

## 6. Web console (FR-7.3, FR-11.5, NFR-13.1)

`kora web` is the React console served by `kora api` ([detailed.md](../architecture/detailed.md) section 7). In MVP it is a full console, not an approvals page (requirements decision 21), held to the user-experience bar of NFR-13.1: one primary task per screen, progressive disclosure of detail, and a design review against that bar before release. Editing graphs in the console stays post-MVP (FR-1.5); graphs are authored in Python and deployed through the CLI or MCP. Every view is a composition of public API routes from section 2, called through the generated TypeScript client; no view needs a route of its own.

| view | what it shows | API routes |
|---|---|---|
| approvals | the pending-approvals list; the approval detail with the context artifact, for a `ReviewBundle` the kora-written `facts` and the agent-written `narrative` rendered visibly apart (DD-33), gate results, each labelled as verified against its protected paths or unprotected (DD-34), metrics, provenance and `run_cost` under facts; the form rendered from its JSON Schema; bound SHA and digests read-only, for an `invalidated` request the observed head SHA next to the bound one and the producing node to retry, for a node failed with `candidate_moved` after approval both SHAs; submit, enabled only for tokens with `approve` | `GET /namespaces/{ns}/approvals?status=pending`, `GET …/approvals/{apr}`, `POST …/approvals/{apr}/respond` |
| runs | the list of runs filtered by graph and status | `GET /namespaces/{ns}/runs?graph=&status=` |
| run | the live graph of the pinned graph version with each node instance's state, fan-out elements and sub-graph attempts, and the run timeline | `GET …/runs/{run}`, `GET …/graphs/{graph}/versions/{gv}` (the IR for the layout), `GET …/runs/{run}/events?follow=true` (`node.*` and `attempt.*` events move node states live) |
| node and attempts | the attempts of a node with their status, strategy, error class and artifacts; artifact payloads and lineage; the diff between two attempts | `GET …/nodes/{node_path}`, `…/attempts[/{att}]`, `…/attempts/{att}/diff?against=`, `GET …/artifacts/{art}[/payload]`, `…/lineage` |
| session log | the normalised events of an attempt, followed live while it runs; the opaque transcript on request | `GET …/attempts/{att}/logs?follow=true[&raw=true]`; needs the `transcripts` capability on the namespace, otherwise the view states that access is missing (`API005`) |
| cost | cost per run, node and attempt, per model, with completeness; the namespace's daily spend against its limit | `GET …/runs/{run}/cost`, `GET /namespaces/{ns}/cost/daily` |
| graphs | the graphs of a namespace, their deployed versions and one version's IR, triggers and their pause state | `GET /namespaces/{ns}/graphs[/{graph}[/versions/{gv}]]`, `GET …/graphs/{graph}/triggers` |

Live views read the newline-delimited JSON streams of `?follow=true` through `fetch` streaming; there is no WebSocket in MVP (section 1). The one mutation in the MVP scope of the console is the approval response; any action added to it later, such as retry or cancel, is the same API operation the CLI and MCP tools call, with the same permission checks.

Login (DD-19, amended 2026-10-16 by ADR-0011): `kora open` requests a one-time login link and opens it in the browser, and `GET /auth/session/{code}` exchanges the single-use code for a session; pasting the API token into the login form (`POST /auth/session`) is the fallback; either way the result is an **opaque server-side session cookie** (`SameSite=Strict`, `Secure`, HTTP-only); the approver recorded on the request is the token's owner (DD-32). Cookie-authenticated mutations carry CSRF protection (an `Origin` check plus a double-submit token), the SPA ships a Content Security Policy, and cookies are never accepted on Bearer-only API paths (design review finding [server-side session cookie](../reviews/review-2026-10-15.md#server-side-session-cookie)). A stale response shows the `API006` message and reloads the request. Every call the console makes is a public API call through the generated TypeScript client.

## 7. Parity checklist (FR-0.3, release gate)

The checklist is executed by a CI job on every release; a failing line blocks the release.

1. **Coverage**: every operation in the OpenAPI document is reachable through at least one CLI command and at least one MCP tool; the test derives the mapping from the shared client's call sites and fails on an uncovered operation (DD-28). The only exclusions are the worker-facing routes (`/workers/*`, `/attempts/*` reporting) and `/auth/session`, which are not user operations; operator operations are covered by `kora_admin` (design review finding [skills and MCP overlap](../reviews/review-2026-10-15.md#skills-and-mcp-overlap)). A **client-side operation** is one that is reachable through the CLI and an MCP tool and has no API operation by design, because the control plane must not perform it; compiling Python sources is its only member in MVP (`kora compile`, `kora_compile`). `test_parity_client_side_operations` checks that both exist and that the OpenAPI document contains no operation that compiles sources, so the route cannot return silently (ADR-0002, amended 2026-10-16).
2. **Schema coverage**: every request and response model used by the CLI, the MCP tools and the worker client is a model the API exports; the TypeScript client is regenerated and diffed.
3. Every operation appears in the skill reference (`kora_reference` topic index) with at least one example.
4. The web console performs every action through the public API (verified by the generated client being its only HTTP dependency).
5. Every error code in the registry has a documented recovery hint and appears in the skill's error table.
6. No operation requires interactive input: CLI prompts are disabled under `--json`; `kora approve` fails with a structured error when required form fields are missing instead of asking.

## 8. Rate and size limits

Request bodies up to 4 MB (an IR with 1,000 nodes fits), except chunked segment, transcript and artifact uploads from workers; oversized tool results inside a segment are cut with a truncation marker; artifact payloads above 1 MB are served by `storage_ref` redirect rather than inline; log streams page at 1,000 events. There are no per-token rate limits in MVP (DD-6).
