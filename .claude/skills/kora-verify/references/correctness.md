# Correctness review

Act as an independent reviewer of the supplied scope. Read `AGENTS.md`, then the affected contracts and cited decisions through `knowledge/README.md`. Run only this profile; do not invoke the parent router or delegate. Do not edit source, commit or publish. Return findings and evidence in the format specified by the parent skill.

Check behavior against the accepted contracts, issue acceptance criteria and complete diff. Identify unowned components and cross-component consequences, including documentation changes. Pending proposals or issues do not override accepted specifications. Review changed verification rules against the stricter approved/base obligations.

- Compiler, IR, CEL and templates: deterministic output, validation/schema agreement, source diagnostics and bounded evaluation. Exercise intricate parsers, arithmetic and state transitions at their natural boundaries.
- Metadata and scheduler: history/result authority, fencing, locks, idempotency, retries, inherited deadlines, stale observations, restart and provider-stall fairness. Database integration runs on both SQLite and PostgreSQL; Linux owns container-dependent cases and macOS the supported non-Docker cases.
- API, Python client, CLI, MCP and generated TypeScript: operation/schema parity, structured errors, streaming, pagination and noninteractive behavior. Product harness skill packages also cover every operation/example, shared-reference generation drift and error/recovery hints; they are installed in user sessions, not graph-node sessions.
- Worker/supervisor and all eleven provider protocols: shared conformance plus integration with real core components and configured protocol fakes. Installed-provider coverage uses a locally built third-party fixture package, discovers `kora.provider` metadata, validates configuration, lists plugins and runs external conformance without core imports.
- Packaging/deployment: installable wheel and web assets, startup/doctor, health/readiness, standalone mode, migrations with active runs and backup/restore against the documented runbook.

Use the repository's real CI/tool commands; do not invent commands for an absent scaffold. Most tests should integrate real components; preserve the no-mocks and deterministic-test rules in `AGENTS.md`. Recorded SDK streams test normalization, not live harness compatibility; supported versions require a separate documented operator probe. Confirm checks cover failures and recovery, not only successful return values. State exactly which obligations remain unimplemented or untested.
