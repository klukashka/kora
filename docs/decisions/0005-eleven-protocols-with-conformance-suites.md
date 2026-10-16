# ADR-0005: Eleven provider protocols, each with a conformance suite, registered through one entry point

- Status: accepted, 2026-10-15; amended 2026-10-16 (the VCS protocol answers the branch head, [ADR-0008](0008-content-bound-approvals.md), issue #16); amended 2026-10-16 (the VCS returns typed checkout and publish plans that the session runner executes; [architecture critique of 2026-10-16, finding A13](../../knowledge/reviews/architecture-critique-2026-10-16.md#a13-remote-composition), issue #20)
- Reviewed: by the project owner, 2026-10-16
- Deciders: project owner, after review of the first design drafts (DD-2, DD-21, DD-22), after the design review of 2026-10-15 (first cycle: DD-29) and after its second cycle (one exporter in MVP)
- Absorbs: DD-2, DD-21, DD-22, the protocol part of DD-29; NFR section 5; requirements decision 14, the "policy protocol in MVP" part of requirements decision 19 and the "OTel as the only MVP exporter" part of requirements decision 20
- Related: [ADR-0001](0001-own-control-plane.md), [ADR-0003](0003-scheduler-and-workers-over-http.md), [ADR-0004](0004-harness-adapters-over-vendor-sdks.md), [ADR-0009](0009-target-repository-trust-boundary.md), [ADR-0011](0011-namespaces-and-personal-tokens.md)

## Context

kora is designed for an instance shared by many teams on an organisation's own infrastructure (NFR-0.1). The first adopter runs an in-house enterprise CI runtime and a non-git version control system, neither of which kora's authors can ship. Extensibility is therefore a release gate: an implementation that passes the conformance suite must run the canonical graph unchanged (NFR-0.2), and MVP acceptance requires the canonical graph to run unchanged on a second runtime/VCS pair written by an adopter (requirements decision 14, which placed the protocols second in the MVP spine). The requirements named nine protocols; `sensor` was added so that waiting on an external condition is pluggable (DD-2), and `policy` after the [design review of 2026-10-15](../../knowledge/reviews/review-2026-10-15.md#central-tool-call-policy) found no central decision point for tool calls (DD-29).

## Decision

Eleven protocols, each a Python `typing.Protocol` with a `name`, a `capabilities()` method returning a frozen dataclass of flags, a `config_schema` for its deployment configuration and a `conformance` marker.

1. **runtime**: where a session executes (`submit` keyed by attempt id, `lookup`, `adopt`, `events`, `cancel`). MVP: `local`, `docker`.
2. **harness adapter**: how a session is driven inside the session runner. MVP: `claude`, `codex`, plus `fake` for CI; `acp` after MVP (ADR-0004).
3. **VCS**: typed checkout and publish plans (`checkout_plan`, `publish_plan`) that the session runner executes inside the runtime, change requests, the branch head, CI status. MVP: `git` with the GitHub and GitLab APIs.
4. **approval channel**: request, poll, feedback, cancel; never decides validity. MVP: `cli`, `vcs`.
5. **secrets**: `resolve(secret://…)` bound to namespace prefixes. MVP: `env`, `file`.
6. **artifact storage**: content-addressed `put`, `get`, `exists`, and an idempotent `delete` used only by retention (added 2026-10-16). MVP: `local` and `s3` (any S3-compatible service).
7. **artifact type**: payload schema, validation, `digest_fields`, `summary`. MVP: the built-in types as data.
8. **observability exporter**: one `on_event` over the append-only event table, replayable, control plane only. MVP: `otel` (OTLP, GenAI semantic conventions) and nothing else; Langfuse and Datadog are OTLP endpoints.
9. **authentication**: `authenticate(request) → Principal`, `revoke`. MVP: `static_token`.
10. **sensor**: `observe(config) → Observation` for `wait_event` nodes and sensor triggers, stateless between polls. MVP: `change_request_status`, `ci_status`, `http`.
11. **policy**: `decide(request, policy, protected_paths) → allow | deny` from the harness's pre-tool-call hook. MVP: `allow_deny`.

**One entry point.** Implementations are discovered through the entry-point group `kora.provider`; each entry point returns validated `ProviderMetadata(protocol, name, implementation, config_schema, version)`. `kora plugins list` prints what is installed; the names are then used in the intermediate representation (IR, for example `runtime.runtime: docker`) or in deployment configuration.

**Capability flags instead of promises.** A runtime declares, among others, `supports_resume`, `enforces_network_policy`, `credential_proxy` and `plan_executors`; `kora validate` emits warning `IR009` when a node requests a capability its runtime lacks. Network policy is enforced by the runtime with the harness sandbox as a second layer (DD-21); when a namespace sets `require_network_enforcement`, a secret-bearing node on a non-enforcing runtime becomes error `IR015`.

**The VCS plans, the runner executes** (amended 2026-10-16). The VCS never touches the workspace: `checkout_plan` and `publish_plan` return typed plans (files to materialise, commands as argv without a shell, credentials as secret references, named captures such as `commit`, `changed_paths` and `diff`), and the session runner executes them inside the runtime, the `CheckoutPlan` as the preparation phase before the harness starts and the `PublishPlan` as the collection phase after the session ends, building the `WorkspaceRef` and the `VcsRef` from the captures and reporting them through the normalised event stream. The runtime names only a `WorkspaceTarget`: the workspace root inside its execution environment and, when it exposes that directory on the worker's host, the host path. Runtime and VCS each declare `plan_executors` (`runner`, `worker_host`); the worker prefers `runner`, and a VCS whose client cannot run inside a sandbox declares `worker_host`, so that the worker executes its plans on its own host where the runtime exposes the workspace there (FR-8.8). `kora validate` emits `IR009` when a node's runtime and VCS declare no common executor; the composition itself is proven by `test_vcs_runtime_cross_product`, which runs one VCS, given no runtime identity, on two runtimes. git declares both executors; `local` declares both, `docker` declares `runner` in MVP.

**Conformance suites.** Each protocol ships `kora.conformance.<protocol>` as pytest parametrised over the entry point under test (`kora conformance runtime inhouse_ci`); the end-to-end suite runs the canonical graph on the fake harness against the runtime and VCS under test. kora's own test doubles are fakes validated by the same suites.

## Alternatives considered

- **Fewer, fatter protocols**, for example a runtime that also checks out or a VCS that also approves. Rejected: the first adopter's VCS client must run outside the sandbox, approvals also arrive through non-VCS channels, and sensors cover conditions beyond any code host.
- **Plugins without conformance tests.** Rejected by NFR-0.2: an adopter must prove a runtime or VCS without kora's authors.
- **One entry-point group per protocol, or pluggy hook wrappers.** Rejected by the [design review of 2026-10-15](../../knowledge/reviews/review-2026-10-15.md#single-provider-entry-point) in favour of a single provider entry point.
- **Harness-native allowlists instead of a policy protocol.** Rejected by the [same review](../../knowledge/reviews/review-2026-10-15.md#central-tool-call-policy): no central decision point, no audit.
- **Vendor-native exporters in MVP.** Rejected: the relevant vendors ingest OTLP.
- **A workspace target kind shared by runtime and VCS** (`host_path`, `volume`, `remote`; the design until 2026-10-16). Replaced: for `remote` the VCS had to drive the runtime's API or hand the runner an untyped bootstrap command, so every VCS had to know every runtime, M × N integrations instead of M + N, and `IR009` proved only that both used the word `remote` ([finding A13](../../knowledge/reviews/architecture-critique-2026-10-16.md#a13-remote-composition)). A typed plan executed by kora's own code is the one mechanism for every runtime.

## Consequences

- Every protocol carries conformance cost, so the surface stays minimal: unread capability flags were dropped in the two review cycles.
- Enterprise implementations run as kora workers inside the adopter's network, connecting outbound only ([ADR-0003](0003-scheduler-and-workers-over-http.md)); all protocols and reference implementations live in the open-source core (NFR-12.1).
- A missing capability is a compile-time warning unless the namespace turns it into an error.
- The MCP server is not a protocol but a mandatory client of the API ([ADR-0002](0002-ai-first-api-and-thin-clients.md)).
- When the runner executes a plan, the VCS credentials are inside the sandbox for the attempt, in the plan steps' environment only, the position harness API keys hold in MVP (DD-30); a VCS that cannot accept it declares `worker_host` and runs in MVP only on `local`, the runtime that exposes the workspace on the worker's host.
- A runtime image must carry the tools the plans require (`git`); a missing tool fails the attempt with `config_error` before anything runs.

## Where it is applied

- [FR-2.3, FR-5.2, FR-7.1, FR-8.1, FR-8.6, FR-8.7, FR-8.8, FR-8a.1, FR-8b.1, FR-10.1, FR-11.3, FR-12.2](../requirements/functional.md)
- [NFR-0.2, NFR-4.5, NFR-5.1, NFR-5.2, NFR-5.4, NFR-10.1, NFR-10.2, NFR-12.1](../requirements/non-functional.md)
- [protocols.md §1 to §13](../../knowledge/specs/protocols.md)
- [ir.md §4.1, §10 (`IR009`, `IR015`)](../../knowledge/specs/ir.md)
- [detailed.md §4, §5 (the preparation and collection phases), §7 (plugins, observability), §8](../../knowledge/architecture/detailed.md)
- [DD-2, DD-21, DD-22, DD-29](../../knowledge/reviews/design-decisions-2026-10-15.md)
