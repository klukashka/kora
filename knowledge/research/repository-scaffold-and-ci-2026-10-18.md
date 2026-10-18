## Research: repository scaffold and CI

Checked on **2026-10-18**, against `master` at `80c1bb9d8b3214ff5e8d852503d47c1f62dd941b`. The repository observations below are the planning baseline. The implementation record identifies the selected Python tools and the checks performed for issue #86.

### Repository findings

- ADR-0013 already settles the language, package manager, build backend and frontend stack. The current architecture reference places packaged frontend output at `src/kora/api/static`.
- The repository contains no Python/frontend manifests, executable tests or `LICENSE`. The current five CI jobs check documentation and schemas.
- Existing [master CI run 39067610021](https://github.com/realkarych/kora/actions/runs/39067610021) succeeded. Its earliest job started at 2026-10-17 20:39:08 UTC and its latest job completed at 20:39:40 UTC: **32 seconds**. This is a documentation-only baseline, not evidence that the proposed build meets the budget.
- The reference deliberately leaves shared-package placement and health serving open. Creating empty domain modules or an invented health endpoint would expand this change.
- Scenario-testing work in [#46](https://github.com/realkarych/kora/issues/46) concerns a later functional slice. This scaffold needs actual packaging tests, not fake providers or metadata fixtures.

### Findings that shape the plan

| Finding | Consequence |
|---|---|
| Default `uv build` builds a wheel from the sdist; explicitly requesting both targets changes that path. | Use the default path to expose missing source-package inputs. |
| An isolated PEP 517 build has its own dependency resolution. A full dev environment can conceal missing build requirements. | Export a hashed build-only constraints file from `uv.lock` and retain build isolation. |
| Ignored generated files need explicit Hatch inclusion. | Mark `src/kora/api/static/**` as wheel artifacts and inspect the resulting archive. |
| Vite transpiles TypeScript without checking types. | Run strict `tsc` separately from `vite build`. |
| Hatch hook documentation and moving upstream source differ in generic interface shape. | Pin Hatchling, type against that installed release, and include the hook in strict Pyright checks. |
| Current pnpm uses `allowBuilds`; old template settings are not interchangeable. | Pin pnpm and explicitly record only the dependency build scripts needed by the actual lockfile. |
| An editable import can resolve the source tree and hide omitted wheel files. | Install a real local wheel into a new environment and run Python in isolation outside the checkout. |
| Broad local Markdown scans encounter installed dependencies and generated files. | Discover repository Markdown through Git, retaining existing intentional research exclusions and coverage of all other project documents. |

Primary references: [uv builds and constraints](https://docs.astral.sh/uv/concepts/projects/build/), [uv lock/sync](https://docs.astral.sh/uv/concepts/projects/sync/), [Hatch build inclusion](https://hatch.pypa.io/latest/config/build/), [Hatch hook interface](https://hatch.pypa.io/latest/plugins/build-hook/reference/), [Vite TypeScript behavior](https://vite.dev/guide/features.html#typescript), [Vite output cleanup](https://vite.dev/config/build-options.html#build-emptyoutdir), [pnpm build settings](https://pnpm.io/settings/build), [pytest integration guidance](https://docs.pytest.org/en/stable/explanation/goodpractices.html).

### Toolchain baseline

- Python **3.11** is the repository's supported floor. The current stable release is **3.15.0**; the proposed matrix checks both endpoints and both operating systems. [Python releases](https://www.python.org/downloads/)
- Node **24** retains the existing CI major version. Select one compatible exact patch for the frontend version file during implementation.
- **uv 0.13.1**, published 2026-10-14, and **pnpm 12.14.0**, published 2026-10-15, are verified release baselines. Dependency resolution and smoke tests still have to establish compatibility. [uv release](https://github.com/astral-sh/uv/releases/tag/0.13.1), [pnpm release](https://github.com/pnpm/pnpm/releases/tag/v12.14.0)
- Keep existing action SHAs; the new pnpm action can use verified `pnpm/action-setup` v6.0.2 at `f68f5791346aaeb8b9d7ea636c1bcf61c15511ed`. Its current interface supports an exact version from `web/package.json` and store caching. Avoid a second cache for the same store. [Action documentation](https://github.com/pnpm/action-setup), [uv Actions guide](https://docs.astral.sh/uv/guides/integration/github/)

Exact package versions are selected with uv/pnpm during implementation and recorded with their rationale. No moving `latest` tool version is part of the new CI contract.

### Scope recommendation

Build the complete packaging path with a minimal React shell and no Python runtime dependencies. Install only Hatchling and source-check/test tooling on the Python side, and React/React DOM plus the frontend build/check dependencies actually used. Add database tooling, provider SDKs, UI libraries and optional runtime extras when their first consumers arrive.

An unrelated distribution already owns [the PyPI name `kora`](https://pypi.org/project/kora/). That does not block local artifacts under the accepted name, but public publishing and generic `pip install kora` instructions must wait for a naming/ownership decision.

The project owner approved the implementation plan in issue #86 on 2026-10-18. This research records implementation evidence rather than changing any architecture decision.

## Python implementation record

Checked on **2026-10-18**. The package starts at `0.1.0.dev0`, requires Python `>=3.11`, and has no runtime dependencies or advertised extras. Runtime libraries and optional extras enter the distribution with their first implemented consumer. Package metadata uses the SPDX expression `Apache-2.0` and includes the complete upstream [Apache license text](https://www.apache.org/licenses/LICENSE-2.0.txt).

| Tool | Exact version | Reason and primary source |
|---|---|---|
| Local Python and minimum CI lane | 3.11.17 | Supported language floor with the current security patch; [official release](https://www.python.org/downloads/release/python-31117/) |
| Upper CI lane | 3.15.0 | Exercise the current stable Python endpoint; [official release](https://www.python.org/downloads/release/python-3150/) |
| uv | 0.13.1 | Approved dependency manager, lock generator and isolated distribution builder; [release](https://github.com/astral-sh/uv/releases/tag/0.13.1) |
| Hatchling | 1.32.4 | ADR-0013 build backend, pinned identically in `build-system.requires` and the `build` dependency group so locked constraints also cover isolated builds; [release metadata](https://pypi.org/pypi/hatchling/1.32.4/json) |
| Ruff | 0.17.1 | One formatter and linter for handwritten Python, including imports, modern syntax, common bugs and annotations; [release metadata](https://pypi.org/pypi/ruff/0.17.1/json) |
| Pyright | 1.1.414 | Strict static typing required by contributor rules; [release metadata](https://pypi.org/pypi/pyright/1.1.414/json) |
| pytest | 9.1.1 | Integration-test runner from ADR-0013; strict configuration/markers, importlib collection and warnings as errors; [release metadata](https://pypi.org/pypi/pytest/9.1.1/json) |

All Python dependencies were added and locked through the pinned uv release. Build and development dependencies are separate groups, with no build backend in the runtime requirements. `uv.lock` records the full transitive graph and hashes. The Python wrapper for Pyright uses Node already required by the frontend build toolchain.

Strict Pyright source inclusion grows with the scaffold: the initial package is included immediately, and the build hook and packaging tests enter the same configuration when those files are implemented. Ruff discovers Python sources, stubs and notebooks throughout the checkout and checks `pyproject.toml`, using the generated-file exclusions. Its default Markdown-fence discovery is deliberately outside the source-code check: specifications and research snippets are checked as documents. No Python source path or lint rule is suppressed.

## Frontend implementation record

Checked on **2026-10-18**. The private `web/` package contains only a React shell identifying kora. It has no API calls, product screens or domain data. The production build writes `index.html` and hashed JavaScript/CSS assets into `src/kora/api/static`; Vite's explicit `emptyOutDir` setting clears only that generated output directory. The `build` command runs the strict browser and Vite-configuration type checks before bundling. The lint command rejects warnings.

| Dependency or tool | Exact version | Reason and primary source |
|---|---|---|
| Node | 24.22.0 | Approved Node major with an exact patch in `web/.node-version`; official macOS ARM64 archive verified against [release checksums](https://nodejs.org/dist/v24.22.0/SHASUMS256.txt) |
| pnpm | 12.14.0 | ADR-0013 frontend dependency manager, pinned in `packageManager` and package engines; [release](https://github.com/pnpm/pnpm/releases/tag/v12.14.0) |
| `react`, `react-dom` | 19.3.1 | ADR-0013 rendering runtime and browser mounting; [React metadata](https://registry.npmjs.org/react/19.3.1), [React DOM metadata](https://registry.npmjs.org/react-dom/19.3.1) |
| `vite` | 8.3.4 | ADR-0013 development server and production bundler; [metadata](https://registry.npmjs.org/vite/8.3.4) |
| `@vitejs/plugin-react` | 6.1.2 | Vite's React integration and refresh support; its peer range includes Vite 8; [metadata](https://registry.npmjs.org/@vitejs/plugin-react/6.1.2) |
| `typescript` | 6.0.3 | Strict application and build-configuration checks; compatible with the parser's `>=4.8.4 <6.1.0` peer range, unlike the current TypeScript 7 release; [metadata](https://registry.npmjs.org/typescript/6.0.3) |
| `@types/react`, `@types/react-dom` | 19.3.0 | React and browser-renderer type definitions aligned with runtime major; [React metadata](https://registry.npmjs.org/@types/react/19.3.0), [React DOM metadata](https://registry.npmjs.org/@types/react-dom/19.3.0) |
| `@types/node` | 24.19.2 | Node types matching the build runtime major; [metadata](https://registry.npmjs.org/@types/node/24.19.2) |
| `eslint` | 10.13.0 | Mandatory frontend source checks; [metadata](https://registry.npmjs.org/eslint/10.13.0) |
| `@eslint/js` | 10.0.1 | Recommended JavaScript rules for the maintained configuration and source; [metadata](https://registry.npmjs.org/@eslint/js/10.0.1) |
| `typescript-eslint` | 8.71.2 | Strict type-aware TypeScript linting using the project service; [metadata](https://registry.npmjs.org/typescript-eslint/8.71.2) |
| `globals` | 17.13.0 | Browser globals for application code and Node globals for build/lint configuration; [metadata](https://registry.npmjs.org/globals/17.13.0) |
| `eslint-plugin-react-hooks` | 7.1.1 | Recommended React hook correctness rules; [metadata](https://registry.npmjs.org/eslint-plugin-react-hooks/7.1.1) |
| `eslint-plugin-react-refresh` | 0.5.7 | Vite refresh-safe component export rules; [metadata](https://registry.npmjs.org/eslint-plugin-react-refresh/0.5.7) |

All frontend dependencies were added with pinned pnpm and exact direct versions. The generated lock records transitives, integrity hashes and platform-specific optional native packages. Vite 8 uses Rolldown and Lightning CSS with published native optional dependencies; this scaffold does not install esbuild. Inspection of the installed manifests found no `preinstall`, `install`, `postinstall` or `binding.gyp` build obligations. Some registry packages retain `prepare` scripts used before publication; these do not execute when installing published registry artifacts.

Consequently, `pnpm-workspace.yaml` explicitly sets `strictDepBuilds: true` and `allowBuilds: {}`. No dependency script is approved or required by this graph. A newly introduced script requires a reviewed decision before installation can succeed. These settings follow the pinned pnpm [build policy](https://pnpm.io/settings/build). An initial inspection install disabled scripts; a subsequent fresh frozen offline install used the committed policy normally, installed all 161 host-compatible packages and left both the lockfile and policy byte-for-byte unchanged. TypeScript and ESLint checks passed with no diagnostics.
