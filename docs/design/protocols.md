# kora MVP design — the eleven protocols and the harness mappings

Status: design draft for owner review (2026-10-15, revised after the design review, see [review-2026-10-15.md](review-2026-10-15.md)). Scope: NFR-5.1–5.2, NFR-10.2, FR-2.3, FR-3.3, FR-5.2, FR-7.1, FR-8.1, FR-10.1, FR-11.3, FR-12.4. Interfaces are Python `typing.Protocol` classes; DTOs are frozen dataclasses or Pydantic models with JSON-only fields (NFR-4.3). Signatures are illustrative; names are the contract. Owner decisions on design choices are referenced as **DD-n** ("design decision n") and recorded in [design-decisions.md](design-decisions.md).

NFR-5.1 lists ten protocols, including **sensor** (DD-2, section 11); the owner added an eleventh, **policy** (DD-29, section 12), and the requirements documents are being updated separately. Harness version pinning per node (former NFR-5.3, NFR-11.2) was removed (DD-14): harness versions are fixed by the installation and only recorded.

## 1. Common rules

- Every protocol has a `name` class attribute, a `capabilities()` method returning a frozen dataclass of flags, a `config_schema` (JSON Schema for its deployment configuration, validated at start-up and shown by `kora plugins list`), and a `conformance` marker that the test suite uses to discover it.
- Implementations are discovered through **one entry-point group**, `kora.provider` (NFR-5.2, review item 24): each entry point returns validated `ProviderMetadata(protocol, name, implementation, config_schema, version)`; `kora plugins list` prints what is installed per protocol; names are then used in the IR (`runtime.runtime: docker`) or in the deployment config:

```toml
[project.entry-points."kora.provider"]
arcadia_ci = "kora_arcadia:runtime_provider"      # returns ProviderMetadata(protocol="runtime", name="arcadia_ci", …)
arc        = "kora_arcadia:vcs_provider"
```

- All methods are `async` and cancellable; every failure is a `KoraProtocolError(code, message, retryable, details)`, and `retryable=True` maps to error class `infrastructure`.
- Implementations receive a `ProtocolContext` (namespace, run id, attempt id, logger, secrets resolver) and never import the scheduler.

## 2. Runtime (FR-8.1)

```python
@dataclass(frozen=True)
class RuntimeCapabilities:
    isolation: Literal["none", "process", "container", "vm", "remote"]
    supports_resume: bool           # can a later attempt reach the same session handle
    enforces_network_policy: bool   # DD-21: the runtime is the enforcement point; the harness sandbox is a second layer
    credential_proxy: bool          # DD-23, DD-30: a per-attempt proxy serves harness API keys; MVP runtimes declare False and inject env vars
    workspace_targets: frozenset[Literal["host_path", "volume", "remote"]]   # DD-22, review item 6
    streams_events: bool            # live event file vs logs available only at end
    max_concurrent: int | None      # advisory capacity
    default_stall_after: timedelta  # DD-15: 15 min unless the runtime says otherwise; nodes override in limits

@dataclass(frozen=True)
class Workload:
    attempt_id: str
    submission_key: str
    lease_epoch: int
    kind: Literal["agent", "command"]
    resolved_config: dict           # the IR node config, templates rendered
    workspace: WorkspaceRef         # from the VCS protocol
    env: dict[str, str]             # the constructed minimal environment for the child process (never the worker's own)
    secret_refs: dict[str, str]     # env var -> secret reference, resolved by the runtime adapter at submit
    harness_credentials: dict[str, str]   # credential name -> secret reference; env var in MVP, proxy when credential_proxy is true (DD-30)
    artifact_inputs: dict[str, ArtifactRef]
    limits: dict                    # timeout, max_events, budget_usd, stall_after
    session_spec: SessionSpec | None   # built by the harness adapter for agent workloads (section 3)

class Runtime(Protocol):
    name: str
    config_schema: dict
    def capabilities(self) -> RuntimeCapabilities: ...
    async def workspace_target(self, attempt_id: str) -> WorkspaceTarget: ...   # where the VCS must check out (section 4)
    async def submit(self, workload: Workload) -> Handle: ...          # idempotent on workload.submission_key
    async def lookup(self, submission_key: str) -> Handle | None: ...  # recovery after crash (FR-6.1a)
    async def adopt(self, handle: Handle) -> AdoptedSession: ...       # re-attach to the event file and heartbeat after restart
    async def status(self, handle: Handle) -> RuntimeStatus: ...       # queued | starting | running | exited(code) | lost
    def events(self, handle: Handle, from_offset: int) -> AsyncIterator[RawEvent]: ...   # tail of the session's .kora/events.jsonl, resumable
    async def heartbeat(self, handle: Handle) -> Liveness: ...         # runtime-level liveness, distinct from the agent loop
    async def cancel(self, handle: Handle, grace: timedelta) -> None: ...
    async def release(self, handle: Handle) -> None: ...               # free workspace and container after artifacts were collected
```

An agent workload is executed inside the runtime by the **session runner**, a small kora entry point (`kora-runner`) present in every runtime image or host install: it loads the harness adapter named in `session_spec`, drives the vendor SDK (section 3), enforces `max_events`, soft budget and `stall_after` from the SDK's event stream, answers policy hooks (section 12) and appends normalised events to `.kora/events.jsonl` in the workspace, the resumable source the worker tails. The runner is kora code, not user code (FR-2.8 still holds). Image digests are recorded on the attempt.

Reference implementations: `local` (subprocess in its own process group tagged with the attempt id; orphaned groups are killed when a worker claims work) and `docker` (one container per attempt, workspace volume, harness binaries and the runner installed in the image). Both declare `credential_proxy = False` in MVP; the per-attempt proxy sidecar (never shared between attempts) is the first post-MVP runtime item together with the cloud-sandbox runtime (DD-30). Enterprise runtimes (Arcadia CI / Sandbox) implement `submit` as a task submission, `events` as log polling, `status` from the task API, and run as a kora worker inside the adopter's network connecting outbound to the API (DD-13). Harness binaries and their versions are part of the runtime installation, not of the node (DD-14).

## 3. Harness adapter (FR-2.1, FR-3.3, FR-6.6, FR-9.1, DD-27)

Reference adapters wrap the **vendor SDKs** instead of parsing CLI output: `claude` uses the Claude Agent SDK for Python (which spawns the pinned `claude` binary and speaks to it over stdio), `codex` uses the Codex SDK over the app-server JSON-RPC protocol, and `acp` speaks the Agent Client Protocol (JSON-RPC over stdio) to any ACP-capable agent (OpenCode, Pi, Gemini CLI and others). kora still enforces `max_events`, the soft budget and `stall_after` itself from the SDK event stream (NFR-8.2).

```python
@dataclass(frozen=True)
class HarnessCapabilities:
    hermetic_mode: bool             # can ignore repo config (setting_sources=[], untrusted project)
    structured_output: bool
    native_budget: bool             # honours a USD cap itself
    resume: bool
    reports_usage_per_turn: bool    # needed for partial usage after a kill
    reports_cost_basis: bool
    policy_hook: Literal["pre_tool_use", "permission_request", "none"]   # how the policy protocol is invoked (section 12)

class HarnessAdapter(Protocol):
    name: str
    config_schema: dict
    def capabilities(self) -> HarnessCapabilities: ...
    def check_config(self, config: dict) -> list[Warning]: ...          # compile-time: fields it cannot honour (IR008); never compares versions (DD-14)
    def build_session_spec(self, config: dict, workspace: WorkspaceRef, feedback: Feedback | None) -> SessionSpec: ...
        # everything the runner needs: SDK options, files to materialise (agents, MCP config, skills, schema), prompt, resume handle
    async def run(self, spec: SessionSpec, policy: PolicyClient, sink: EventSink) -> SessionResult: ...
        # executed by the runner inside the runtime: opens the SDK session, forwards every SDK message as a NormalisedEvent
        # (session_started | turn | tool_call | tool_result | permission_denied | usage | result | error) to the sink,
        # routes pre-tool-call hooks to `policy`, honours sink.enforcement() (terminate on max_events / budget / stall)
    def finalise(self, events: list[NormalisedEvent], outcome: RunnerOutcome) -> SessionResult: ...
        # AgentSession fields, StructuredOutput payload, usage records with completeness, stop reason, error class
    def installed_version(self) -> str: ...     # from the SDK's init message; recorded on AgentSession, never enforced (DD-14)
```

A fake adapter (`fake`) replays recorded SDK event streams for CI (NFR-10.1). Adapters are importable inside runtime images because the runner executes them there.

## 4. VCS (FR-5.2)

```python
class Vcs(Protocol):
    name: str
    def capabilities(self) -> VcsCapabilities: ...   # change_requests, review_comments, ci_status, head_sha_watch
    async def checkout(self, repo: str, rev: str | VcsRef, into: WorkspaceTarget) -> WorkspaceRef: ...
    async def publish(self, workspace: WorkspaceRef, branch_hint: str) -> VcsRef: ...        # commit + push, returns commit SHA
    async def open_change_request(self, ref: VcsRef, title: str, body: str) -> VcsRef: ...  # adds url
    async def change_request_status(self, ref: VcsRef) -> ChangeRequestStatus: ...          # open | approved | changes_requested | merged | closed, head_sha, reviews[]
    async def post_comment(self, ref: VcsRef, body: str) -> str: ...                        # used by the VCS approval channel to publish a ReviewBundle
    async def ci_status(self, ref: VcsRef) -> CiStatus: ...
    async def release(self, workspace: WorkspaceRef) -> None: ...
```

`WorkspaceTarget` is named by the runtime and says where the VCS must place the checkout (DD-22). Target kinds in v1 (review item 6):

| kind | meaning | used by |
|---|---|---|
| `host_path` | a directory on the machine the worker runs on | `local` runtime; `docker` when bind-mounting |
| `volume` | a named volume the runtime mounts into the session container; the VCS writes through a helper container or the worker | `docker` |
| `remote` | an opaque id the runtime provisions (sandbox, CI task); the VCS performs the checkout through the runtime's own API, or the runner performs it inside the session from a bootstrap command the VCS returns | enterprise CI, cloud sandboxes, runtimes without a shared filesystem |

A runtime advertises the kinds it supports in `workspace_targets`; a VCS advertises the kinds it can fill in `VcsCapabilities.workspace_targets`; `kora validate` emits `IR009` when a graph's runtime and VCS share no kind. The git reference implementation supports all three; `arc` is the adopter's and supports whatever Arcadia's runtime declares. VCS results written to events and logs are redacted of tokens; pushes use credential helpers, never secrets embedded in URLs (review item 21).

## 5. Approval channel (FR-7.1, FR-7.2a)

```python
class ApprovalChannel(Protocol):
    name: str
    def capabilities(self) -> ApprovalCapabilities: ...   # push_notifications, carries_identity, observes_head_sha
    async def request(self, req: ApprovalRequestDto) -> str: ...
        # req: approval_request_id (idempotency key), form schema, context artifact, bound commit SHA and digests, expires_at
    async def poll(self, channel_request_id: str) -> ApprovalResponseDto | None: ...
        # response carries: payload, approver identity, timestamp, observed head SHA and digests
    async def notify(self, channel_request_id: str, event: Literal["invalidated", "expired", "cancelled"]) -> None: ...
    async def cancel(self, channel_request_id: str) -> None: ...
```

Built-in: `cli` (the API itself is the channel; `poll` reads the row) and `vcs` (publishes the `ReviewBundle` as a comment, maps review states to decisions, returns the head SHA with every response so the scheduler can refuse stale ones). The channel never decides validity; the scheduler compares SHA and digests.

## 6. Secrets (FR-10.1)

```python
class SecretsProvider(Protocol):
    name: str
    config_schema: dict             # includes `namespace_prefixes`: namespace -> allowed reference prefixes
    async def resolve(self, reference: str, ctx: ProtocolContext) -> SecretValue: ...   # secret://<provider>/<name>; refuses references outside ctx.namespace's prefixes
    async def list_names(self, prefix: str) -> list[str]: ...                           # for validation of aliases, never values
```

Secret references are **bound to the namespace** (review item 11): the provider configuration maps each namespace to the prefixes it may reference; `kora validate` enforces it (`IR007`) and `resolve` enforces it again at submission time. Resolution happens inside the runtime adapter at submission time; `SecretValue` registers itself with the redaction filter that every captured stream, error detail and VCS result passes through (FR-10.2). Built-in: `env`, `file`.

## 7. Artifact storage (FR-4.4)

```python
class ArtifactStorage(Protocol):
    name: str
    async def put(self, payload: bytes | AsyncIterator[bytes], digest: str, content_type: str) -> StorageRef: ...
    async def get(self, ref: StorageRef) -> AsyncIterator[bytes]: ...
    async def exists(self, digest: str) -> StorageRef | None: ...      # content-addressed dedupe
    async def presign(self, ref: StorageRef, ttl: timedelta) -> str | None: ...   # None when unsupported; API then proxies
```

Built-in: `local` (directory tree by digest). S3-compatible is post-MVP but the interface is final.

## 8. Artifact type (FR-4.1–4.3)

```python
class ArtifactType(Protocol):
    name: str                       # "VcsRef" or "namespace/Name"
    parent: str | None
    schema: dict                    # JSON Schema of the payload
    def validate(self, payload: dict) -> list[ValidationError]: ...
    def digest_fields(self, payload: dict) -> list[str]: ...     # which fields participate in gate digests (FR-7.2a)
    def summary(self, payload: dict) -> dict: ...                # small projection for branch expressions and the API list view
```

Built-ins ship as data (schemas) with a generic implementation; custom types are registered through the API (`PUT /artifact-types`) as schema plus optional entry point for custom `summary`.

## 9. Observability exporter (FR-11.3, NFR-9.2)

```python
class ObservabilityExporter(Protocol):
    name: str
    config_schema: dict
    async def on_event(self, event: EventRow) -> None: ...              # every row of the append-only event table, in order
    async def on_session_event(self, attempt_id: str, event: NormalisedEvent) -> None: ...   # the live session stream
    async def flush(self) -> None: ...
```

Exporters consume the `event` table (review item 6), so an export can be **replayed** from any point by re-reading rows; the scheduler feeds them through a bounded queue with drop-with-metric and an exporter never blocks a transition. The built-in `otel` exporter follows the GenAI semantic conventions (review item 22): a run is an `invoke_workflow` span with `gen_ai.workflow.name = <graph id>`; an attempt is an `invoke_agent` span, `CLIENT` kind when the harness runs remotely or managed, `INTERNAL` otherwise, with `gen_ai.usage.*` from the usage records; `gen_ai.conversation.id` is the harness session id, so kora spans join with Claude Code's own OpenTelemetry output on `session.id`. Langfuse and Datadog exporters follow the same interface.

## 10. Authentication (FR-12.4, NFR-4.4)

```python
class Authenticator(Protocol):
    name: str
    config_schema: dict
    async def authenticate(self, request: RequestInfo) -> Principal: ...   # kind (user | service), owner, namespaces, operations, and for service: runtime
    async def revoke(self, token_id: str) -> None: ...
```

Two principal kinds (review item 11): a **user** principal is a per-namespace token from the `api_token` table with scopes and expiry; a **service** principal belongs to a worker and may only claim attempts of its runtime and heartbeat, upload and transition attempts whose lease it holds (checked by lease epoch). Built-in: `static_token` (MVP). OIDC and RBAC implement the same `Principal` so routes do not change. There are no tokens for agent nodes: nodes never call kora (DD-5).

## 11. Sensor (FR-2.3, FR-6.2, DD-2)

A sensor observes an external condition on behalf of a `wait_event` node or a sensor trigger. It is polled by the scheduler at the configured interval; it holds no worker and keeps no state between polls beyond what it returns.

```python
@dataclass(frozen=True)
class SensorCapabilities:
    supports_push: bool             # can also deliver observations as inbound events (webhook), not only by poll
    min_interval: timedelta         # the scheduler refuses shorter intervals
    needs_vcs: bool                 # built-ins change_request_status and ci_status

class Sensor(Protocol):
    name: str
    config_schema: dict             # JSON Schema for `poll.config`; validated at kora validate (IR003-style error with YAML path)
    def capabilities(self) -> SensorCapabilities: ...
    async def observe(self, config: dict, inputs: dict, ctx: ProtocolContext) -> Observation: ...
        # returns a JSON object exposed to the `until` CEL expression as `observation`, plus an optional `cursor` the scheduler passes back next time
```

Built-ins: `change_request_status` and `ci_status` (through the VCS protocol; observation is the `ChangeRequestStatus` or `CiStatus` DTO), `http` (GET a URL with optional headers from secret references; observation is `{status, body}`). Adopters register sensors in the `kora.sensor` entry-point group and reference them by name in `poll.sensor`.

## 12. Policy (DD-29)

The policy protocol is the **decision point for tool calls**. The session runner invokes it from the harness's pre-tool-call hook (Claude Agent SDK `PreToolUse` hook callback, Codex app-server `item/permissions/requestApproval`, ACP `session/request_permission`) before every tool call; a deny stops the call and is recorded as a `policy.denied` event and on the `AgentSession` artifact.

```python
@dataclass(frozen=True)
class ToolCallRequest:
    harness: str
    tool: str                       # harness tool name, e.g. "Bash", "Edit", "mcp__yt__query"
    arguments: dict
    workspace: WorkspaceRef
    node_path: str

@dataclass(frozen=True)
class PolicyDecision:
    allow: bool
    reason: str

class Policy(Protocol):
    name: str
    config_schema: dict             # schema of the `policy` object nodes may carry
    def validate(self, policy: dict) -> list[ValidationError]: ...        # kora validate, IR013
    async def decide(self, request: ToolCallRequest, namespace_policy: dict, node_policy: dict, ctx: ProtocolContext) -> PolicyDecision: ...
```

Two policies apply to every call: the **namespace policy** set by the platform team (`namespace.policy.tool_policy`) and the node's `policy` field, inherited through the preset layers; the namespace is evaluated first and a deny from either wins. MVP ships `allow_deny` (ordered allow and deny pattern lists in the harness's tool-pattern syntax); Cedar and OPA arrive as plugins through the same interface. The policy runs inside the runner, so the decision is local to the session and never requires the agent to reach kora.

## 13. Conformance suites (NFR-10.2)

Each protocol ships `kora.conformance.<protocol>` as pytest parametrised over the entry point under test (`kora conformance runtime arcadia_ci`). Outline:

- **Runtime**: one handle for two submits with one key; `lookup` after restart; `adopt` resumes the event file from the stored offset without duplicate or lost lines; `cancel` within grace; `status` reaches `exited`; the child process sees only the constructed environment; declared capabilities hold (`enforces_network_policy` blocks a disallowed host, `credential_proxy` keeps the key out of the session environment, each declared workspace target kind is fillable by the reference git VCS).
- **Harness adapter**: recorded SDK event streams yield the expected normalised events, usage completeness, `max_events` termination at the exact count, structured output, hermetic behaviour (a repo hook that must not run), policy hook invoked before every tool call, installed version recorded.
- **VCS**: checkout at SHA; `publish` returns the SHA `change_request_status` reports as head; two attempts never share a workspace; `release` leaves nothing; comment round-trip.
- **Approval channel**: request → `None` → response with identity → stale response reported with its observed SHA → cancel and notify idempotent.
- **Secrets**: resolution, missing-reference error shape, redaction in a captured stream.
- **Artifact storage**: content-addressed dedupe, streaming beyond memory, `presign` or `None`.
- **Artifact type**: validation errors carry JSON paths; `digest_fields` stable across key order.
- **Observability**: a slow exporter does not delay a run; drops are counted.
- **Authentication**: scope enforcement per operation; revoked tokens are refused.
- **Sensor**: `config_schema` rejects bad config with a path; `observe` is side-effect free when called twice; `cursor` round-trips; an `until` expression over the documented observation shape evaluates; `min_interval` is honoured by the scheduler.
- **Policy**: `validate` rejects a malformed policy with a path; a namespace deny beats a node allow; decisions are deterministic for identical requests; the denied call never reaches the tool (checked with the fake harness).
- **End to end**: the canonical graph on the fake harness against the runtime and VCS under test (NFR-0.2).

## 14. Harness mapping tables (FR-3.3, DD-27)

Resolved node-config field → SDK option. "kora" means enforced by the runner from the event stream regardless of harness; "warn" means the field cannot be honoured and `check_config` emits `IR008`.

| config field | Claude Agent SDK (`ClaudeAgentOptions`, `query` / `ClaudeSDKClient`) | Codex SDK over app-server (`thread/start`, `turn/start`) | ACP (`initialize`, `session/new`, `session/prompt`) |
|---|---|---|---|
| `prompt` | `query(prompt=…)` | `turn/start` input | `session/prompt` |
| `model` | `model` | thread config `model` | agent-specific session config; warn when the agent exposes none |
| `system_prompt_append` | `system_prompt={type: preset, preset: claude_code, append: …}` | `model_instructions_file` override (replace semantics; adapter prepends the Codex defaults) | warn; adapter prepends to the first prompt |
| `skills` | materialised skill directories through `add_dirs` with `setting_sources=[]` | `skills.config[]` config override (DD-20) | warn unless the agent reads skills from the workspace |
| `subagents` | `agents={…}` | warn | warn |
| `hooks` | `hooks={…}` Python callbacks | `hooks` config when `features.hooks` | warn |
| `policy` (DD-29) | `PreToolUse` hook callback → policy protocol | `approval_policy = on-request`; `item/permissions/requestApproval` → policy protocol | `session/request_permission` → policy protocol |
| `mcp_servers` | `mcp_servers={…}` (pinned digests verified by the runner) | `mcp_servers.<id>` config | `session/new mcpServers` |
| `allowed_tools` / `disallowed_tools` | `allowed_tools`, `disallowed_tools` | `permissions` profile approximation; pattern-level rules warn and are enforced by the policy hook | enforced by the policy hook only |
| `permission_mode` | `permission_mode` (`plan`, `acceptEdits`, `dontAsk`, `bypassPermissions`) | `approval_policy` (`never` for `dont_ask`/`accept_edits`), `bypass` → `danger-full-access` | `session/set_mode` where the agent offers modes; else warn |
| `sandbox` | `sandbox=SandboxSettings(…)` | `sandbox_mode` (`read-only`, `workspace-write`, `danger-full-access`) | warn; runtime isolation only |
| `trust_repo_config: false` | `setting_sources=[]`, auto-memory disabled, per-attempt config dir | user config ignored, project untrusted, `project_doc_max_bytes=0` | hermetic workspace overlay; warn when the agent reads project config regardless |
| `trust_repo_config: true` | `setting_sources=[project]` | project trusted | agent default |
| `network_policy` | runtime (DD-21); additionally `sandbox.network.allowed_domains` | runtime; additionally `sandbox_workspace_write.network_access` | runtime only |
| `output_schema` | `output_format={type: json_schema, schema}` → `ResultMessage.structured_output` | `turn/start outputSchema` → final message | warn; the runner validates the final text as JSON, else `artifact_invalid` |
| installed version (recorded only, DD-14) | SDK `init` message | `initialize` response | `initialize` response `agentInfo` |
| `limits.stall_after` | kora, from message timestamps, extended during an open tool call | kora | kora |
| `limits.timeout` | kora: `interrupt()` then process kill after grace | kora: `turn/interrupt` then kill | kora: `session/cancel` then kill |
| `limits.max_events` | kora, counting assistant messages, `tool_use` and `tool_result` blocks | kora, counting `item/completed` notifications | kora, counting `session/update` tool calls and agent messages |
| `limits.budget_usd` | `max_budget_usd` **and** kora from per-model usage on the result | kora from `turn/completed.usage` × price table | kora from usage in `session/update` when present |
| usage source | `ResultMessage` per-model usage with cost basis (includes subagents) | `turn/completed.usage` summed per turn; `partial` if killed mid-turn | agent-dependent; `unavailable` when absent |
| session handle (`resume`) | `resume=<session id>` | `thread/resume` | `session/load` when the agent declares `loadSession` |
| `env` / `secret_env` | `env=` (the runner builds a minimal environment) | `shell_environment_policy` + constructed env | constructed env of the agent process |
| harness credential (DD-30) | `ANTHROPIC_API_KEY` in the constructed env; base URL to the proxy post-MVP | `CODEX_API_KEY` in the constructed env | agent-specific variable |
| billing class | auth-method probe on the init message | auth file inspection; default `api_key` | `subscription` unless the adapter config declares otherwise |
| stop reason / error class | `ResultMessage.subtype` (`success`, `error_max_budget_usd` → `budget_exhausted`, `error_during_execution` → `harness_error`) | `turn/failed`, `error` → `harness_error` | `stopReason` (`end_turn`, `max_tokens`, `cancelled`, `refusal` → `harness_error`) |

Fields with the same semantics on all three are the kora vocabulary; fields that only one harness has are mapped on that harness and warned on the others, never silently dropped (FR-3.3).
