# kora MVP design — design decisions

Status: decided by the owner on 2026-10-15, after review of the first design drafts (DD-1 to DD-26) and after three independent design reviews (DD-27 to DD-31; the review findings and their dispositions are in [review-2026-10-15.md](review-2026-10-15.md)). This file keeps the numbering the design documents cite (**DD-n**, "design decision n"); the numbers are stable identifiers grouped by topic, not a sequence (DD-10 was never used). Each item states the decision and where it is applied. Nothing here is open.

## Graph model and IR

**DD-1. Trigger rules.** `none_failed_min_one_success` is the seventh rule, in the closed enum of IR v1. Applied: [ir.md](ir.md) §3, [ir.schema.json](ir.schema.json) `triggerRule`.

**DD-2. Sensors are a protocol.** The tenth protocol, `sensor`, with entry points in `kora.sensor`; built-ins `change_request_status`, `ci_status` (through the VCS protocol) and `http`. `wait_event` nodes and sensor triggers reference a sensor by name with a `config` object and an `until` expression over the observation. Applied: [protocols.md](protocols.md) §11 (interface, capability flags, conformance), [ir.md](ir.md) §4.3 and §8, schema `sensorPoll`, [scheduler.md](scheduler.md) §2 and §5, [data-model.md](data-model.md) `trigger_binding`. The requirements documents (NFR-5.1 lists nine protocols) are updated separately by the owner.

**DD-3. Expression language is CEL.** Branch conditions, retry predicates, `approve_when`, event `match` and sensor `until` are CEL expressions. After the review: the reference implementation uses the Rust-backed `common-expression-language` binding with kora's own schema-driven checker and the CEL conformance suite in CI, and the DSL takes CEL strings only (no expression builder). Applied: [ir.md](ir.md) §7 and §10 (`IR011`), [dsl.md](dsl.md) §1 and §2, [architecture.md](architecture.md) §7, examples.

**DD-8. No transform node kind, no helper CLI.** Packing a candidate, selecting the best candidate and assembling the `ReviewBundle` are ordinary agent nodes on a cheap model over typed inputs; a command node serves authors who want a fixed formula. kora attaches the run's aggregated cost to the approval request itself (after the review: the plain sum of the run's usage records at request time, no lineage walk). Applied: [ir.md](ir.md) §3 and §4.2, [examples/yql-optimization.ir.yaml](examples/yql-optimization.ir.yaml) (`accept`, `select_best`, `bundle` as agent nodes on the `packer` preset), [data-model.md](data-model.md) §5 and `approval_request.attempt_cost`, [scheduler.md](scheduler.md) §2, [api-and-agent-surface.md](api-and-agent-surface.md) approvals route and frontend, [dsl.md](dsl.md) §1.

**DD-9. Fan-out body is always a subgraph.** The compiler wraps a single node. After the review there is no `fan_in` node: the `fan_out` node declares the collected `List[T]` outputs, and a body is fed only through the node's `inputs` with `over` naming the mapped one. Applied: [ir.md](ir.md) §4.4, schema `fanOutConfig`, `outputDecl.collect`, [dsl.md](dsl.md) §1, examples.

**DD-11. Prompt templates are full Jinja2 in a sandbox.** Sandboxed environment, no filesystem loaders, imports or includes. Applied: [ir.md](ir.md) §7 and §10 (`IR012`), [dsl.md](dsl.md) §1 and §2 (`DSL012`), examples.

**DD-12. Trigger overlap default is `allow`** for every trigger kind; `skip` and `queue` are per-graph options (`graph.overlap`). Applied: [ir.md](ir.md) §2 and §8, schema `graph.overlap`, [scheduler.md](scheduler.md) §2, examples.

## Data model and scheduling

**DD-4. Retention.** Graph-level setting with namespace default. Defaults: run metadata and usage records forever; session transcripts 90 days; artifact payloads 180 days; `VcsRef` and other reference-only artifacts forever. Payloads only are deleted; metadata stays. After the review the deletion runs as a separate batched low-priority task with reference counting on `storage_ref` and the states `present` → `expiring` → `expired`. Applied: [data-model.md](data-model.md) §10 and `payload_state` columns, [ir.md](ir.md) §2 `runtime.retention`, schema `retention`, [scheduler.md](scheduler.md) §13, [dsl.md](dsl.md) `@graph(retention=…)`.

**DD-7. Temporal-backed execution backend** is prototyped after MVP, by demand; the backend boundary stays documented, after the review as prose rather than a Python interface with one implementation. Applied: [scheduler.md](scheduler.md) §12, [data-model.md](data-model.md) §9.

**DD-13. Scheduler and workers are separate processes.** The scheduler decides and persists; a worker per runtime claims attempts and streams events; workers may run inside an adopter's network and connect outbound only. After the review, workers have exactly one path to the control plane, the HTTP API, fenced by a lease epoch; standalone runs the same client over an in-process ASGI transport. Applied: [scheduler.md](scheduler.md) §1 and §3, [protocols.md](protocols.md) §2, [architecture.md](architecture.md) §2 and §7, [data-model.md](data-model.md) §1.

**DD-14. No harness version pin on nodes.** Harness versions are fixed by the installation (images, installed binaries). `harness_version` is removed from node config; the adapter records the installed version on `AgentSession` and the attempt and never compares it to a node requirement (a `resume` whose installed version differs from the previous attempt's falls back to `fresh_with_feedback`). Applied: [ir.md](ir.md) §4.1, schema `agentConfig`, [protocols.md](protocols.md) §3 and §14, [dsl.md](dsl.md) §3, [data-model.md](data-model.md) `attempt`, [scheduler.md](scheduler.md) §7, examples. Former NFR-5.3 and NFR-11.2 are being removed by the owner.

**DD-15. `stall_after`** has a runtime-level default (15 minutes) with a graph-level value in `runtime.stall_after` and a per-node override in `limits.stall_after`. Applied: [ir.md](ir.md) §2, §3 and §9, schema `limits` and `runtimeSection`, [scheduler.md](scheduler.md) §4, [protocols.md](protocols.md) `RuntimeCapabilities.default_stall_after`.

## API, clients and agent operation

**DD-5 and DD-16. Removed: agent nodes never operate kora.** kora's MCP server and skills exist only for the user's own coding-agent session, replacing manual actions (author, compile, deploy, trigger, inspect, approve, retry, cancel). The MCP server acts with the user's token and has exactly the user's permissions; there is no operator allowlist, no per-attempt token, no child-run cost attribution and no recursion limit. Applied: [api-and-agent-surface.md](api-and-agent-surface.md) (introduction, §1, §4, §5; former §7 deleted), [protocols.md](protocols.md) §10, [data-model.md](data-model.md) (`run.parent_attempt_id` and attempt tokens removed), [scheduler.md](scheduler.md) §6, [ir.md](ir.md) §4.1, [dsl.md](dsl.md) §5. FR-0.5 is being removed by the owner.

**DD-6. No per-token rate limits in MVP.** Applied: [api-and-agent-surface.md](api-and-agent-surface.md) §8.

**DD-17. Webhook authentication** supports both an HMAC body signature (Standard Webhooks shape, secret from the secrets provider) and a bearer token; the source picks per trigger. Applied: [ir.md](ir.md) §8, schema `trigger.auth`, [api-and-agent-surface.md](api-and-agent-surface.md) §1 (`API007`) and §2, [data-model.md](data-model.md) `inbound_event.auth_kind`, [scheduler.md](scheduler.md) §2, examples.

**DD-18. MCP server in two forms**: a `kora mcp` stdio process talking to the API with the user's token, and HTTP MCP endpoints on the API server (`/mcp/authoring`, `/mcp/operating` after DD-28). Applied: [api-and-agent-surface.md](api-and-agent-surface.md) §2 and §4.

**DD-19. Frontend login**: the API token pasted into a login form; the approver is recorded as the token owner. After the review the browser keeps an opaque server-side session cookie (`SameSite=Strict`, `Secure`) rather than the token itself, with CSRF protection and CSP, and cookies are never accepted on Bearer-only API paths. Applied: [api-and-agent-surface.md](api-and-agent-surface.md) §2 and §6, [architecture.md](architecture.md) §7, [data-model.md](data-model.md) `api_token.owner`.

## Protocols and harness mapping

**DD-20. Codex skills are first-class** (`skills.config[]` in `config.toml`), so skills map natively on both harnesses. Applied: [protocols.md](protocols.md) §14 mapping table, [dsl.md](dsl.md) §5, [api-and-agent-surface.md](api-and-agent-surface.md) §5.

**DD-21. Network policy is enforced by the runtime** (Docker rules, enterprise sandbox policy) with the harness sandbox as an additional layer; compile-time warning `IR009` when the runtime declares it cannot enforce. Applied: [protocols.md](protocols.md) §2 and §14, [ir.md](ir.md) §4.1 and §10.

**DD-22. Workspace target kinds.** The VCS checks out into a `WorkspaceTarget` named by the runtime; runtime and VCS pairs must share at least one kind. Kinds after the review: `host_path`, `volume`, `remote` (opaque; covers provisioned workspaces and in-session bootstrap). Applied: [protocols.md](protocols.md) §2 (`workspace_targets`) and §4 (table), `IR009` in [ir.md](ir.md) §10.

**DD-23. Harness credentials** reach the session through a credential proxy when the runtime declares `credential_proxy`, otherwise through environment variables; both paths are in the runtime protocol. Scoped by DD-30 for MVP. Applied: [protocols.md](protocols.md) §2 (`RuntimeCapabilities.credential_proxy`, `Workload.harness_credentials`) and §14, [ir.md](ir.md) §4.1.

## DSL

**DD-24. Keyword-only inputs** for node calls (`review(change=…, run=…)`); positional arguments only for subgraph calls with declared parameters; a positional argument to a node is `DSL003`. Applied: [dsl.md](dsl.md) §1 and §2.

**DD-25. Presets live in both places**: Python objects in graph code and registry presets published through the API (`presets.registry(name, version=…)`), both snapshotted into the IR at compile time (under `meta.presets` after the review; registry presets are pulled by `kora presets pull` so compile stays offline). Applied: [dsl.md](dsl.md) §1 to §3, [ir.md](ir.md) §2, [api-and-agent-surface.md](api-and-agent-surface.md) §3, [data-model.md](data-model.md) `preset`.

**DD-26. `@graph(repo=…)` is optional**; a node without any workspace source is compile error `DSL003`. Applied: [dsl.md](dsl.md) §1 and §2.

## Decisions after the design review (2026-10-15)

**DD-27. Harness adapters wrap the vendor SDKs.** The reference `claude` adapter uses the Claude Agent SDK for Python, the `codex` adapter the Codex SDK over the app-server JSON-RPC protocol, and a third built-in adapter speaks ACP (Agent Client Protocol, JSON-RPC over stdio) to OpenCode, Pi, Gemini and other ACP agents; no CLI JSONL parsing. kora still enforces `max_events`, the soft budget and `stall_after` itself from the SDK event stream, inside a session runner that executes in the runtime. Binaries remain fixed per installation image (DD-14). Applied: [protocols.md](protocols.md) §2 (runner), §3 and §14, [architecture.md](architecture.md) §4, §5 and §7, [ir.md](ir.md) §4.1 (`harness: acp`).

**DD-28. Curated MCP servers.** Two servers, `kora-authoring` and `kora-operating`, with 13 task-shaped tools in total; long operations (`kora_plan`, `kora_trigger`, `kora_retry`) are MCP Tasks per the 2026-07 specification; skills are also published over MCP as resources and prompts. Parity (FR-0.3) is verified by a coverage test that every API operation is reachable through at least one CLI command and one MCP tool, plus schema-coverage checks, not by generating one tool per operation. Applied: [api-and-agent-surface.md](api-and-agent-surface.md) §4, §5 and §7, [architecture.md](architecture.md) §2, §7 and §8, [dsl.md](dsl.md) §5.

**DD-29. Policy protocol.** An eleventh protocol, `policy`, is the decision point for tool calls, invoked by the session runner through the harness's pre-tool-call hook (Claude Agent SDK `PreToolUse`, Codex `item/permissions/requestApproval`, ACP `session/request_permission`). Nodes carry a `policy` field inherited through the preset layers; the platform team sets a namespace policy that is evaluated first; a deny from either wins. MVP implements allow/deny lists; Cedar and OPA follow as plugins. Applied: [protocols.md](protocols.md) §12, §13 and §14, [ir.md](ir.md) §4.1 and §10 (`IR013`), schema `toolPolicy`, [data-model.md](data-model.md) `namespace.policy.tool_policy` and `policy.denied` events, [dsl.md](dsl.md) §3, [architecture.md](architecture.md) §2, §4 and §7, examples.

**DD-30. Harness credentials in MVP are environment variables.** The `credential_proxy` runtime capability stays in the protocol, but the reference runtimes declare it false; the per-attempt proxy (never shared between attempts) is the first post-MVP runtime item, alongside the cloud-sandbox runtime. Applied: [protocols.md](protocols.md) §2 and §14, [ir.md](ir.md) §4.1.

**DD-31. Daily soft cost limit per namespace in MVP.** One policy field, `daily_budget_usd`; when the day's estimated spend exceeds it, no new attempts are admitted and running ones finish; the state is exposed in `/metrics` and on `GET /namespaces/{ns}/cost/daily`. Applied: [scheduler.md](scheduler.md) §6 and §11, [data-model.md](data-model.md) `namespace`, [api-and-agent-surface.md](api-and-agent-surface.md) §2 and §4.

Confirmed unchanged by the review: DD-11 (full Jinja2 sandbox for prompt templates) and the post-MVP status of the Kubernetes runtime (FR-8.3).
