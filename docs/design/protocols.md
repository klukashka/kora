# kora MVP design — the ten protocols and the harness mappings

Status: design draft for owner review (2026-10-15). Scope: NFR-5.1–5.2, NFR-10.2, FR-2.3, FR-3.3, FR-5.2, FR-7.1, FR-8.1, FR-10.1, FR-11.3, FR-12.4. Interfaces are Python `typing.Protocol` classes; DTOs are frozen dataclasses or Pydantic models with JSON-only fields (NFR-4.3). Signatures are illustrative; names are the contract. Owner decisions on formerly open choices are referenced as **OQ-n** and recorded in [design-decisions.md](design-decisions.md).

NFR-5.1 lists ten protocols, including **sensor** (DD-2, section 11), and the requirements documents are being updated separately. Harness version pinning per node (former NFR-5.3, NFR-11.2) was removed (DD-14): harness versions are fixed by the installation and only recorded.

## 1. Common rules

- Every protocol has a `name` class attribute, a `capabilities()` method returning a frozen dataclass of flags, and a `conformance` marker that the test suite uses to discover it.
- Implementations are discovered through **entry points** (NFR-5.2) in the group `kora.<protocol>` and selected by name in the IR (`runtime.runtime: docker`) or in the deployment config:

```toml
[project.entry-points."kora.runtime"]
arcadia_ci = "kora_arcadia.runtime:ArcadiaRuntime"
[project.entry-points."kora.vcs"]
arc = "kora_arcadia.vcs:ArcVcs"
```

- All methods are `async` and cancellable; every failure is a `KoraProtocolError(code, message, retryable, details)`, and `retryable=True` maps to error class `infrastructure`.
- Implementations receive a `ProtocolContext` (namespace, run id, attempt id, logger, secrets resolver) and never import the scheduler.

## 2. Runtime (FR-8.1)

```python
@dataclass(frozen=True)
class RuntimeCapabilities:
    isolation: Literal["none", "process", "container", "vm", "remote"]
    supports_pause: bool
    supports_snapshot: bool
    supports_resume: bool           # can a later attempt reach the same session handle
    enforces_network_policy: bool   # DD-21: the runtime is the enforcement point; the harness sandbox is a second layer
    credential_proxy: bool          # DD-23: harness API keys are served by a proxy inside the runtime, never placed in the session's env
    workspace_targets: frozenset[WorkspaceTargetKind]   # DD-22: which checkout targets this runtime can hand to the VCS
    streams_events: bool            # live JSONL vs logs available only at end
    max_concurrent: int | None      # advisory capacity
    default_stall_after: timedelta  # DD-15: 15 min unless the runtime says otherwise; nodes override in limits

@dataclass(frozen=True)
class Workload:
    attempt_id: str
    submission_key: str
    kind: Literal["agent", "command"]
    resolved_config: dict           # the IR node config, templates rendered
    workspace: WorkspaceRef         # from the VCS protocol
    secret_refs: dict[str, str]     # env var -> secret reference (injected as env vars by the runtime adapter)
    harness_credentials: dict[str, str]   # credential name -> secret reference; served through the proxy when credential_proxy is true, else as env vars (DD-23)
    artifact_inputs: dict[str, ArtifactRef]
    limits: dict                    # timeout, max_events, budget_usd
    harness_invocation: HarnessInvocation | None   # built by the harness adapter for agent workloads

class Runtime(Protocol):
    name: str
    def capabilities(self) -> RuntimeCapabilities: ...
    async def submit(self, workload: Workload) -> Handle: ...          # idempotent on workload.submission_key
    async def lookup(self, submission_key: str) -> Handle | None: ...  # recovery after crash (FR-6.1a)
    async def adopt(self, handle: Handle) -> AdoptedSession: ...       # re-attach event stream and heartbeat after restart
    async def status(self, handle: Handle) -> RuntimeStatus: ...       # queued | starting | running | exited(code) | lost
    def events(self, handle: Handle) -> AsyncIterator[RawEvent]: ...   # stdout/stderr/JSONL lines with offsets, resumable from an offset
    async def heartbeat(self, handle: Handle) -> Liveness: ...         # runtime-level liveness, distinct from the agent loop
    async def cancel(self, handle: Handle, grace: timedelta) -> None: ...
    async def release(self, handle: Handle) -> None: ...               # free workspace and container after artifacts were collected
```

Reference implementations: `local` (subprocess, host checkout, `credential_proxy = false`) and `docker` (one container per attempt, workspace volume, harness binary installed in the image, optional credential proxy sidecar that holds the API key and forwards requests so the key never enters the container). Enterprise runtimes (Arcadia CI / Sandbox) implement `submit` as a task submission, `events` as log polling, `status` from the task API, and run as a kora worker inside the adopter's network connecting outbound to the control plane (DD-13). Harness binaries and their versions are part of the runtime installation (image or host install), not of the node (DD-14).

## 3. Harness adapter (FR-2.1, FR-3.3, FR-6.6, FR-9.1)

```python
@dataclass(frozen=True)
class HarnessCapabilities:
    hermetic_mode: bool             # can ignore repo config (--bare)
    structured_output: bool
    native_budget: bool             # honours a USD cap itself
    native_turn_limit: bool
    resume: bool
    reports_usage_per_turn: bool    # needed for partial usage after a kill
    reports_cost_basis: bool

class HarnessAdapter(Protocol):
    name: str
    def capabilities(self) -> HarnessCapabilities: ...
    def check_config(self, config: dict) -> list[Warning]: ...          # compile-time: fields it cannot honour (IR008); never compares versions (DD-14)
    def build_invocation(self, config: dict, workspace: WorkspaceRef, feedback: Feedback | None) -> HarnessInvocation: ...
        # argv, env (secret refs only), stdin, files to materialise (settings JSON, agents JSON, MCP config, schema)
    def parse_event(self, raw: RawEvent) -> NormalisedEvent | None: ...  # session_started | turn | tool_call | tool_result | permission_denied | usage | result | error
    def enforce(self, state: EnforcementState, event: NormalisedEvent) -> Enforcement | None: ...
        # counts events and spend; returns Terminate(reason) for max_events / budget breaches (FR-6.6, FR-9.4)
    def finalise(self, events: list[NormalisedEvent], exit_code: int) -> SessionResult: ...
        # AgentSession fields, StructuredOutput payload, usage records with completeness, stop reason, error class
    def installed_version(self, probe_output: str) -> str: ...  # parses `claude --version` / `codex --version`; recorded on AgentSession and the transcript, never enforced (DD-14)
```

A fake adapter (`fake`) replays scripted event files for CI (NFR-10.1).

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

`WorkspaceTarget` is named by the runtime and says where the VCS must place the checkout (DD-22). Target kinds in v1:

| kind | meaning | used by |
|---|---|---|
| `host_path` | a directory on the machine the worker runs on | `local` runtime; `docker` when bind-mounting |
| `volume` | a named volume the runtime mounts into the session container; the VCS writes through a helper container or the worker | `docker` |
| `remote_workspace` | an opaque id of a workspace the runtime itself provisions (sandbox, CI task); the VCS performs the checkout through the runtime's own API or by handing the runtime a revision | enterprise CI, cloud sandboxes |
| `in_session` | the checkout is performed inside the session by a bootstrap command the VCS returns | runtimes without a shared filesystem |

A runtime advertises the kinds it supports in `workspace_targets`; a VCS advertises the kinds it can fill in `VcsCapabilities.workspace_targets`; `kora validate` emits `IR009` when a graph's runtime and VCS share no kind. The git reference implementation supports `host_path`, `volume` and `in_session`; `arc` is the adopter's and supports whatever Arcadia's runtime declares.

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
    async def resolve(self, reference: str, ctx: ProtocolContext) -> SecretValue: ...   # secret://<provider>/<name>
    async def list_names(self, prefix: str) -> list[str]: ...                           # for validation of aliases, never values
```

Resolution happens inside the runtime adapter at submission time; `SecretValue` registers itself with the redaction filter that every captured stream passes through (FR-10.2). Built-in: `env`, `file`.

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
    async def on_run_event(self, event: RunEvent) -> None: ...
    async def on_node_event(self, event: NodeEvent) -> None: ...
    async def on_attempt_event(self, event: AttemptEvent) -> None: ...
    async def on_session_event(self, attempt_id: str, event: NormalisedEvent) -> None: ...
    async def on_artifact(self, artifact: ArtifactVersionDto) -> None: ...
    async def flush(self) -> None: ...
```

The scheduler feeds exporters through a bounded in-process queue with drop-with-metric; an exporter never blocks a transition. Built-in: `otel` (GenAI semantic conventions: `invoke_agent` span per attempt, `gen_ai.usage.*`); Langfuse and Datadog exporters follow the same interface.

## 10. Authentication (FR-12.4, NFR-4.4)

```python
class Authenticator(Protocol):
    name: str
    async def authenticate(self, request: RequestInfo) -> Principal: ...   # owner identity, kind (user | service), namespaces, operations
    async def revoke(self, token_id: str) -> None: ...
```

Built-in: `static_token` (MVP). OIDC and RBAC implement the same `Principal` so routes do not change. There are no tokens for agent nodes: nodes never call kora (DD-5).

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

## 12. Conformance suites (NFR-10.2)

Each protocol ships `kora.conformance.<protocol>` as pytest parametrised over the entry point under test (`kora conformance runtime arcadia_ci`). Outline:

- **Runtime**: one handle for two submits with one key; `lookup` after restart; `adopt` resumes the stream from the stored offset without duplicate or lost lines; `cancel` within grace; `status` reaches `exited`; declared capabilities hold (`enforces_network_policy` blocks a disallowed host, `credential_proxy` keeps the key out of the session environment, each declared workspace target kind is fillable by the reference git VCS).
- **Harness adapter**: scripted JSONL fixtures yield the expected normalised events, usage completeness, `max_events` termination at the exact count, structured output, hermetic behaviour (a repo hook that must not run), installed version recorded.
- **VCS**: checkout at SHA; `publish` returns the SHA `change_request_status` reports as head; two attempts never share a workspace; `release` leaves nothing; comment round-trip.
- **Approval channel**: request → `None` → response with identity → stale response reported with its observed SHA → cancel and notify idempotent.
- **Secrets**: resolution, missing-reference error shape, redaction in a captured stream.
- **Artifact storage**: content-addressed dedupe, streaming beyond memory, `presign` or `None`.
- **Artifact type**: validation errors carry JSON paths; `digest_fields` stable across key order.
- **Observability**: a slow exporter does not delay a run; drops are counted.
- **Authentication**: scope enforcement per operation; revoked tokens are refused.
- **Sensor**: `config_schema` rejects bad config with a path; `observe` is side-effect free when called twice; `cursor` round-trips; an `until` expression over the documented observation shape evaluates; `min_interval` is honoured by the scheduler.
- **End to end**: the canonical graph on the fake harness against the runtime and VCS under test (NFR-0.2).

## 13. Harness mapping tables (FR-3.3)

Resolved node-config field → harness flag. "kora" means enforced by kora from the event stream regardless of harness; "warn" means the field cannot be honoured and `check_config` emits `IR008`.

| config field | Claude Code (`claude --bare -p … --output-format stream-json --verbose`) | Codex (`codex exec --json …`) |
|---|---|---|
| `prompt` | positional prompt (stdin when large) | positional prompt (`-` for stdin) |
| `model` | `--model` | `--model` / `-c model=` |
| `system_prompt_append` | `--append-system-prompt-file` | `-c model_instructions_file=` (replace semantics; adapter prepends Codex defaults; warn if `replace` differs) |
| `skills` | `--add-dir <skills dir>` with materialised `SKILL.md`, invoked by name in the prompt | `-c skills.config=[…]` entries pointing at materialised skill directories (Codex skills are a first-class config unit, DD-20) |
| `subagents` | `--agents <json file>` | warn (no subagent definitions in `codex exec`) |
| `hooks` | `--settings <json>` `hooks` block | `-c hooks=…` when `features.hooks` is enabled; else warn |
| `mcp_servers` | `--mcp-config <file> --strict-mcp-config` | `-c mcp_servers.<id>.…` per server |
| `allowed_tools` / `disallowed_tools` | `--allowedTools` / `--disallowedTools` | `-c permissions…` profile approximation; pattern-level rules warn |
| `permission_mode` | `plan`→`--permission-mode plan`, `accept_edits`→`acceptEdits`, `dont_ask`→`--permission-mode dontAsk --permission-prompts none`, `bypass`→`bypassPermissions` | `plan`→warn, `accept_edits`/`dont_ask`→`--ask-for-approval never`, `bypass`→`--dangerously-bypass-approvals-and-sandbox` |
| `sandbox` | `--settings` `sandbox` block (`read_only` → filesystem deny-write, `workspace_write` → default, `full_access` → `sandbox.enabled=false`) | `--sandbox read-only` / `workspace-write` / `danger-full-access` |
| `trust_repo_config: false` | `--bare` (no CLAUDE.md, hooks, MCP, skills from the repo) | `--ignore-user-config --ignore-rules`, project `.codex/` untrusted, `AGENTS.md` suppressed by `project_doc_max_bytes=0` |
| `trust_repo_config: true` | omit `--bare`, `--setting-sources project` | default trust for the workspace path |
| `network_policy` | enforced by the runtime (DD-21); additionally `--settings` `sandbox.network.allowedDomains` | enforced by the runtime; additionally `-c sandbox_workspace_write.network_access` (no allowlist in the harness) |
| `output_schema` | `--json-schema <file>` → `structured_output` in the result | `--output-schema <file>` + `-o <file>` |
| installed version (recorded only, DD-14) | `claude --version` probe → `AgentSession.harness_version` | `codex --version` probe → `AgentSession.harness_version` |
| `limits.stall_after` | kora, silence in the stream (DD-15) | kora |
| `limits.timeout` | kora (SIGTERM, then SIGKILL after grace) | kora |
| `limits.max_events` | kora, counting `assistant`, `tool_use` and `tool_result` events; `--max-turns` not relied on | kora, counting `turn.*` and `item.completed` events |
| `limits.budget_usd` | `--max-budget-usd` **and** kora from `result.modelUsage` / streamed usage | kora only, from `turn.completed.usage` × price table |
| usage source | `result.modelUsage[model]` (includes subagents), `costBasis`, `total_cost_usd` | `turn.completed.usage` summed per turn; `completeness=partial` if killed mid-turn |
| session handle (`resume`) | `result.session_id`; resume with `--resume <id>` and the feedback message | `thread.started.thread_id`; `codex exec resume <id> <feedback>` |
| `env` / `secret_env` | process env | process env |
| harness credential | `ANTHROPIC_API_KEY` as env var, or `ANTHROPIC_BASE_URL` pointed at the runtime's credential proxy when `credential_proxy` is declared (DD-23) | `CODEX_API_KEY` as env var, or provider `base_url` override to the proxy |
| billing class | `claude auth status` → `authMethod` | auth file inspection; default `api_key` |
| stop reason / error class | `result.subtype` (`success`, `error_max_budget_usd`→`budget_exhausted`, `error_during_execution`→`harness_error`), exit code 143→`cancelled` | `turn.failed`/`error` events→`harness_error`; exit code |

Fields with the same semantics on both are the kora vocabulary; fields that only one harness has are mapped on that harness and warned on the other, never silently dropped (FR-3.3).
