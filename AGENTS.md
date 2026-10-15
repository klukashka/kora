# AGENTS.md

Rules for every coding agent and human contributor in this repository. They take precedence over the defaults and habits of any model or tool. If a rule blocks the task, stop and say so instead of working around it.

## Project

kora is an open-source (Apache-2.0) control plane that runs graphs of coding-agent sessions. Documentation has two layers. `docs/` is written for people: `positioning.md`, `requirements/functional.md`, `requirements/non-functional.md`, the two-page `architecture.md`, the decision records in `decisions/` and the deferred changes in `proposals/`. `knowledge/` is the reference layer loaded section by section: the normative specifications in `knowledge/specs/`, the detailed diagrams, stack table and glossary in `knowledge/architecture/detailed.md`, review records, analyses and research. `knowledge/README.md` says which file to load for which task. Read the specification of a component and the decision records it cites before implementing it; when a specification and a decision record disagree, the decision record wins.

## Code

- **No comments and no docstrings** in hand-written source code: Python, TypeScript, SQL, shell, Alembic migrations. Code documents itself through names, types, small functions and explicit data structures. When you feel a comment is needed, rename, extract a function, or introduce a type or an enum instead.
- Tool directives count as comments and are forbidden too: no `# noqa`, `# type: ignore`, `# pyright: ignore`, `// eslint-disable`, `// @ts-ignore`, `// @ts-expect-error`. Fix the code, or disable the rule for the whole project in the tool's configuration. The only allowed comment-like line is a shebang in an executable script.
- Generated files (lockfiles, the generated OpenAPI TypeScript client) are exempt because nobody edits them by hand. Configure generators and templates (for example Alembic's `script.py.mako`) so they do not emit comments or docstrings into files that are then edited by hand.
- **All Python code is fully type-annotated**: every parameter, every return value (including `-> None`), every class and dataclass attribute, and module-level variables whose type is not obvious from the literal. No implicit `Any`, and no explicit `typing.Any` either: parse untyped data at the boundary into Pydantic models or precise types.
- **Static checks are strict and mandatory**: pyright in strict mode and ruff (lint and format) over all Python; TypeScript with `strict: true` and ESLint over `web/`. Code that does not pass them is not finished.
- **Fail fast.** No bare `except:`, no `except Exception` that swallows, logs-and-continues or returns a placeholder, no silent fallback values that hide a failure. Catch only what you can handle meaningfully and let everything else propagate. Every error that leaves the HTTP API is a structured error as specified in `knowledge/specs/api-and-agent-surface.md`.
- **The HTTP API is the single implementation** (ADR-0002). CLI, MCP servers and the web frontend are thin clients over the shared API client and contain no logic of their own. A new or changed API operation ships in the same change with its CLI command and MCP tool, and the parity coverage test passes.
- **Dependencies are deliberate.** Add one only when the standard library or an existing dependency cannot reasonably do the job, and record it with its reason in the stack table in `knowledge/architecture/detailed.md`; a dependency that changes a decided row of ADR-0013 needs a new decision record. Python dependencies are managed only with `uv` and frontend dependencies only with `pnpm`; `uv.lock` and `pnpm-lock.yaml` are committed and change only through those tools.

## Documentation

- Everything committed to the repository is in English.
- Write every specification, design document, decision record, commit message and PR description for a reader who never saw the session that produced it. Never reference session-local context: subagent names, review rounds, ad-hoc labels such as "item K2", "question 5" or "hypothesis H3", "as discussed above", "the user asked". Give hypotheses, decisions, files and folders descriptive names.
- Identifiers that are defined inside the repository and resolve there (for example `FR-2.1`, `NFR-10.1`, `DD-12`) are allowed; labels that exist only in a conversation are not.
- **Documents and code change together.** A change that alters behaviour described in a specification, decision record or requirement updates that document in the same commit. When code and its specification disagree and the task does not say which one is right, stop and ask instead of silently following either.

## Testing

- **No mocks.** Forbidden: `unittest.mock`, `MagicMock`, `pytest-mock`, `monkeypatch`, patching modules, attributes or environment variables at runtime, stubbing HTTP clients at the transport level, and any other technique that replaces behaviour behind the code's back.
- **Fakes are the only test doubles.** A fake is a real implementation of a kora protocol (the fake harness adapter and fake runtime from `NFR-10.1` are the reference examples), selected through configuration or constructor injection and validated by the same conformance suite as production implementations. If code cannot be tested without patching, change its design so the dependency is injected.
- **Unit tests are rare.** Write them only for intricate logic where a bug hides easily: formulas and arithmetic (cost accounting, limits), parsers and checkers (CEL type checking, IR validation), state machines (attempt and retry transitions). Do not unit-test glue code, data mapping, trivial accessors or framework behaviour.
- **Integration tests are the bulk.** Exercise real components together: the real FastAPI application, the real metadata database, the real scheduler and worker, with fake harness and fake runtime where a model or sandbox would otherwise be needed.
- **Both databases.** Every integration test that touches the metadata database runs against PostgreSQL and against SQLite (standalone mode) through one parametrised fixture, and must behave the same on both.
- **Tests are deterministic.** No `sleep` for synchronisation: wait on an explicit condition with a deadline. No real network beyond local containers and in-process servers, and no calls to real model providers or code hosts. Time and randomness come from injected sources, and tests pass fake ones. A flaky test is a bug: fix its cause, never hide it with retries, reruns or longer timeouts.
- **E2E tests are targeted.** A small set drives kora through its public surfaces (CLI and HTTP API) and covers every main happy-path use case end to end.
- **CI finishes in under 10 minutes** wall-clock for the whole pipeline. Write tests so they can run in parallel. When a change threatens the budget, make tests faster or consolidate them; never drop coverage silently.

## Platforms

- Supported operating systems: **Linux and macOS**. Windows is not supported: no Windows code paths, compatibility shims, path handling or CI jobs. POSIX assumptions are fine.
- CI runs on Linux and macOS. GitHub-hosted macOS runners have no Docker, so tests that need Docker (Docker runtime, PostgreSQL through containers) run on the Linux job only.

## Definition of done

- Before reporting a task as done, run the same checks CI runs (format, lint, type check, all tests) and see them pass. Report failures as they are.
- Never make checks green by weakening them: no `skip` or `xfail` on failing tests, no deleted or loosened assertions, no relaxed linter, type-checker or test settings, no raised timeouts or CI budget, unless changing that check is the task itself.
