# Declarative specification formats for agent/workflow definitions and the "code-first DSL → YAML/JSON IR" pattern (as of October 2026)

Scope note: notes cover (a) YAML/JSON agent & workflow specs, (b) code-first SDKs compiling to a declarative IR with multi-language parity, (c) UI editing of code-defined graphs and round-tripping, (d) typed artifact/schema conventions between steps. All findings carry inline sources; version/date stamps are given where the source states them. Several vendor doc sites moved during 2025–2026 (docs.claude.com → code.claude.com; google.github.io/adk-docs → adk.dev; serverlessworkflow.io → open-workflow-specification.org; githubnext.github.io/gh-aw → github.github.io/gh-aw; developers.openai.com/codex → learn.chatgpt.com), which is itself a signal of how fast this landscape churns.

---

## KQ1. Which existing spec is closest to "a DAG of coding-agent sessions with per-node skills/subagents/hooks/MCP config and inheritance", and what does it look like concretely?

### Takeaway
No single spec covers the whole target. The closest building blocks are: **GitHub Agentic Workflows (gh-aw)** for "one agent session = one job, with engine/tools/MCP/skills/plugins/network/safe-outputs declared in YAML frontmatter and composed via `imports:` with explicit merge rules"; **Claude Code's subagent `.md` frontmatter + plugin manifest** for the richest per-node vocabulary (tools, model, permissionMode, skills, mcpServers, hooks, memory, isolation: worktree, effort); **Google ADK Agent Config YAML** for a Pydantic-schema-backed agent tree (`sub_agents` via `config_path`) with Sequential/Parallel/Loop composition; and **Windmill OpenFlow** for a general DAG-of-steps IR with subflows, loops, branches, retries, suspend/approval and an `aiagent` module type. The DAG-level glue (edges, typed artifacts between nodes) comes from the workflow world (OpenFlow, Serverless Workflow DSL, Argo), not from any agent framework.

### Cited Findings

**GitHub Agentic Workflows (gh-aw) — markdown + YAML frontmatter, compiled to GitHub Actions**
- A workflow is a Markdown file whose YAML frontmatter documents fields grouped as: metadata (`description`, `intent`, `labels`, `metadata`, `source`), triggers (`on`, `if`), sharing (`imports`, `import-schema`), engine (`engine` incl. `engine.harness`, `engine.model-routing`, `engine.driver`, `engine.extensions`; `runtimes`), access/network (`permissions`, `github-app`, `network`, `checkout`, `secrets`, `environment`), tools (`tools` with `edit`, `bash`, `github.toolsets`; `skills`, `plugins`, `mcp-scripts`, `safe-outputs`), execution (`pre-steps`, `steps`, `pre-agent-steps`, `post-steps`, `jobs`, `runs-on`, `timeout-minutes`, `concurrency`, `container`, `services`, `cache`), env (`env`, `excluded-env`, `observability`), limits (`max-turns`, `max-ai-credits`, `max-daily-ai-credits`, `user-rate-limit`), plus `features` and `strict` — [gh-aw Frontmatter reference](https://github.github.io/gh-aw/reference/frontmatter/)
- Built-in "harnesses" named in the reference: `copilot`, `claude`, `codex`; Pi via `engine.extensions`, Copilot SDK driver mode via `engine.driver`; sidebar also lists Gemini and "Agy" as experimental — [gh-aw Frontmatter reference](https://github.github.io/gh-aw/reference/frontmatter/)
- Example workflow file (from the reference page):
  ```markdown
  ---
  description: "Reviews pull requests and posts feedback"
  on:
    pull_request:
      types: [opened]
  engine: copilot
  permissions:
    contents: read
    pull-requests: write
  tools:
    edit:
    bash: ["gh pr view"]
    github:
      toolsets: [default]
  network:
    allowed: [defaults]
  imports:
    - shared/common-tools.md
  safe-outputs:
    create-issue:
      max: 1
  timeout-minutes: 30
  strict: true
  ---
  # PR Feedback
  Review the pull request and summarize risks, missing tests, and style issues.
  ```
  — [gh-aw Frontmatter reference](https://github.github.io/gh-aw/reference/frontmatter/)
- Inheritance/composition via `imports:`: shared files (those without `on:`) may define `tools`, `mcp-servers`, `safe-outputs`, `network`, `permissions`, `runtimes`, `steps`, `env`, `engine.mcp`; other fields warn and are ignored. Paths resolve relative to the importer; `.github/`- or `/`-prefixed paths resolve from repo root; remote imports use `owner/repo/path@ref` (tag/branch/SHA); `#Section` suffix imports one section; `?` suffix makes an import optional; parameterized imports use `uses`/`with` validated against the shared file's `import-schema`; a file may appear only once in the import graph; circular imports fail — [gh-aw Imports reference](https://github.github.io/gh-aw/reference/imports/)
- Merge rules per field (documented table): `tools` deep-merge, `allowed` arrays concatenated+deduped, other MCP tool conflicts fail; `mcp-servers` imported servers override same-named main servers, first import wins among imports; `network.allowed` unioned, main `mode`/`firewall` win; `permissions` validation-only (main must grant ≥ imported); `safe-outputs` main overrides imported types, duplicate types across imports fail; `runtimes` main overrides, imports fill gaps; `env` main wins, duplicate keys across imports fail; `services` all merge, duplicate names fail; `engine.mcp` first import wins, engine itself always from the importer; `steps`/`pre-agent-steps` prepended in dependency order, `post-steps` appended. Among imports most scalars are first-wins, direct imports discovered before nested — [gh-aw Imports reference](https://github.github.io/gh-aw/reference/imports/)
- The page is internally inconsistent about whether shared `jobs:` merge into the lock file — [gh-aw Imports reference](https://github.github.io/gh-aw/reference/imports/)
- Compilation (`gh aw compile`) has five phases: parsing+validation (schema checks, expression safety, import resolution), job construction, dependency resolution, action pinning, YAML generation. The `.lock.yml` contains a metadata header, a Mermaid dependency graph, alphabetically ordered jobs (`pre_activation`, `activation`, `agent`, `detection`, safe-output jobs, `conclusion`) and the embedded original prompt. Frontmatter is embedded at compile time; the markdown body is loaded at runtime, so "Compilation is only required when changing frontmatter configuration". Actions are pinned to commit SHAs, resolved from `.github/aw/actions-lock.json` cache → GitHub API → embedded pins — [gh-aw Compilation process](https://github.github.com/gh-aw/reference/compilation-process/)
- Package versions: Go module `github.com/github/gh-aw` had releases v0.80.x (June 2026) through v0.82.4 (July 2026) — [pkg.go.dev gh-aw v0.82.4](https://pkg.go.dev/github.com/github/gh-aw@v0.82.4/pkg/workflow)
- Documentation moved from `githubnext.github.io/gh-aw` to `github.github.io/gh-aw` (old URL shows only a redirect notice) — [githubnext.github.io/gh-aw](https://githubnext.github.io/gh-aw/)

**Claude Code subagent files, plugin manifests, hooks, settings**
- Subagent = Markdown file with YAML frontmatter; body is the system prompt; only `name` and `description` required. Fields: `tools` (allowlist), `disallowedTools`, `model` (`sonnet`/`opus`/`haiku`/`fable`/full ID/`inherit`), `permissionMode` (`default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, `plan`), `maxTurns`, `skills` (preloaded into context), `mcpServers` (name reference or inline definition), `hooks` (`PreToolUse`/`PostToolUse`/`Stop` scoped to the subagent), `memory` (`user`/`project`/`local`), `background`, `isolation: worktree`, `effort`, `color`, `initialPrompt`, `omitClaudeMd`, `autoCompactWindow`, `experimental.cacheTtl`. Unrecognized fields are ignored without error — [Claude Code: Subagents](https://code.claude.com/docs/en/sub-agents)
- Example:
  ```markdown
  ---
  name: code-reviewer
  description: Reviews code for quality and best practices. Use after code changes.
  tools: Read, Glob, Grep
  model: sonnet
  ---
  You are a code reviewer...
  ```
  — [Claude Code: Subagents](https://code.claude.com/docs/en/sub-agents)
- Location precedence (highest first): managed settings → `--agents` CLI JSON → `.claude/agents/` → `~/.claude/agents/` → plugin `agents/`. Identity comes only from `name`. Inheritance: tools inherit from the parent minus a fixed removed set; `permissionMode` inherits if unset and is ignored when parent runs in `bypassPermissions`/`acceptEdits`/auto; model resolved per-invocation → frontmatter → `CLAUDE_CODE_SUBAGENT_MODEL` → parent model. Plugin-shipped subagents ignore `hooks`, `mcpServers`, `permissionMode` for security — [Claude Code: Subagents](https://code.claude.com/docs/en/sub-agents)
- Subagents can also be defined without files: `claude --agents '{"code-reviewer": {"description": ..., "prompt": ..., "tools": [...], "model": "sonnet"}}'` (JSON, session-only) — [Claude Code: Subagents](https://code.claude.com/docs/en/sub-agents)
- Plugin manifest `.claude-plugin/plugin.json` (optional; `name` the only required key). Fields: `$schema`, `name`, `displayName`, `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords`, `metadata`, `defaultEnabled`, `dependencies` (`"name"`, `"name@marketplace"`, or `{name, marketplace, version}`), `settings` (only `agent` and `subagentStatusLine` take effect), `userConfig`, `channels`, `skills`, `commands`, `agents`, `hooks`, `mcpServers` (json path, `.mcpb`/`.dxt` bundle path or URL, or inline map), `lspServers`, `outputStyles`, `workflows` (`.js`), `experimental.{themes,monitors,evals}` — [Claude Code: Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference)
- Standard layout: `.claude-plugin/plugin.json`, `skills/<name>/SKILL.md`, `commands/`, `agents/`, `hooks/hooks.json`, `.mcp.json`, `.lsp.json`, `output-styles/`, `workflows/`, `themes/`, `monitors/monitors.json`, `bin/`, `settings.json`. Combination rules: `commands`/`agents`/`outputStyles`/`workflows` *replace* defaults; `skills` *adds*; `hooks`/`mcpServers`/`lspServers` *merge* (default file first, later same-named server wins) — [Claude Code: Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference)
- Hooks file shape: `{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/scripts/check-command.sh"}]}]}}`; inline hooks in the manifest omit the wrapper. Path variables: `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`, `${CLAUDE_PROJECT_DIR}`; user options via `${user_config.KEY}` / `CLAUDE_PLUGIN_OPTION_<KEY>` — [Claude Code: Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference)
- Marketplace entry vs manifest: `strict` (default true) lets the entry append `commands`/`agents`/`skills`/`outputStyles`/`themes`; for `hooks` the entry's matchers for an event replace the manifest's for that event; `strict:false` + overlapping component keys = "conflicting manifests" load failure. Precedence: entry `defaultEnabled`/display fields override manifest; manifest `version` overrides entry — [Claude Code: Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference)
- Settings scopes: user `~/.claude/settings.json`, project `.claude/settings.json`, local `.claude/settings.local.json`, managed (org). Agent-relevant keys: `permissions.{allow,ask,deny,defaultMode,additionalDirectories}`, `hooks` (+ `disableAllHooks`, `allowManagedHooksOnly`), `env`, `model`, `agent` (start every session as a named subagent), `enabledPlugins`, `extraKnownMarketplaces`, `sandbox.*`, `enableAllProjectMcpServers`, `allowedMcpServers`/`deniedMcpServers`/`managedMcpServers`. MCP server definitions live in `.mcp.json`, not settings — [Claude Code: Settings reference](https://code.claude.com/docs/en/settings-reference)

**Agent Skills spec (SKILL.md) and AGENTS.md**
- SKILL.md = YAML frontmatter + Markdown. Frontmatter: `name` (required, 1–64 chars, lowercase/digits/hyphens, must match directory name), `description` (required, ≤1024 chars), `license`, `compatibility` (≤500 chars), `metadata` (string→string map), `allowed-tools` (space-separated, experimental, e.g. `Bash(git:*) Bash(jq:*) Read`). Optional dirs: `scripts/`, `references/`, `assets/`. Progressive disclosure: metadata (~100 tokens) at startup, body (<5000 tokens recommended) on activation, resources on demand; keep SKILL.md under 500 lines. Validator: `skills-ref validate ./my-skill` — [agentskills.io Specification](https://agentskills.io/specification)
- AGENTS.md: plain Markdown with no required fields; nearest file in directory tree wins (monorepo nesting), explicit chat prompts override everything; "over 60k open-source projects" use it; stewarded by the Agentic AI Foundation under the Linux Foundation; emerged from OpenAI Codex, Amp, Jules, Cursor, Factory; supported by Codex, Jules, Factory, Aider, goose, opencode, Zed, Warp, VS Code, Devin, Cursor, Gemini CLI, GitHub Copilot coding agent, Windsurf and others — [agents.md](https://agents.md/)

**Codex config.toml**
- `~/.codex/config.toml` (user) and `.codex/config.toml` (project, loaded only for trusted projects; cannot set provider/auth/notify/profile/otel keys). Profiles are separate files `$CODEX_HOME/<profile>.config.toml` selected with `--profile`. Top-level keys include `model`, `model_provider`, `model_providers.<id>`, `approval_policy` (`on-request`/`never`/granular table), `sandbox_mode` (`read-only`/`workspace-write`/`danger-full-access`), `permissions.<name>` (named permission profiles with `extends`), `mcp_servers.<id>` (stdio: `command`,`args`,`cwd`,`env`,`env_vars`; HTTP: `url`, `bearer_token_env_var`, `http_headers`, `auth`, `oauth.*`; `enabled_tools`/`disabled_tools`, `tools.<tool>.approval_mode`, `startup_timeout_sec` default 10, `tool_timeout_sec` default 60, `required`), `hooks.<Event>[].hooks[]` (events: `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `SubagentStart`, `SubagentStop`, `UserPromptSubmit`, `Stop`, `Interrupt`; command and MCP-tool handlers run, prompt/agent handlers skipped; enabled by `features.hooks`), `skills.config[]{path,enabled}`, `skills.max_context_tokens`, `plugins.<plugin>.enabled`, `marketplaces.<name>.{source,source_type,ref,sparse_paths}`, `features.*` (`apps`, `hooks`, `memories`, `multi_agent`, ...), `projects.<path>.trust_level`. Editor schema directive: `#:schema https://developers.openai.com/codex/config-schema.json` — [Codex config reference](https://learn.chatgpt.com/docs/config-file/config-reference)

**CrewAI**
- New default project format is JSON-first: `crew.jsonc` at project root plus `agents/<name>.jsonc`, `tools/`, `knowledge/`, `skills/`; `crewai create crew` generates it by default; `--classic` yields the old `crew.py` + `config/agents.yaml` + `config/tasks.yaml` with `@CrewBase`/`@agent`/`@task`/`@crew` decorators (still supported). Flow projects are a separate type embedding crews in `crews/` — [CrewAI: Crews](https://docs.crewai.com/edge/en/concepts/crews); [CrewAI: Prepare for deployment](https://docs.crewai.com/edge/en/enterprise/guides/prepare-for-deployment)
- `crew.jsonc` keys: `name`, `agents` (list of agent names → `agents/<name>.jsonc`), `tasks[]` (`name`, `description`, `expected_output`, `agent`, `context` = earlier task names, `output_file`), `process` (`sequential`/`hierarchical`), `verbose`, `memory`, `inputs`, plus `cache`, `max_rpm`, `planning`, `manager_llm`, `manager_agent`, callbacks. Python references use `{"python": "module.attribute"}`; custom tools `"custom:<name>"` load from `tools/<name>.py` (docs warn these execute local code) — [CrewAI: Crews](https://docs.crewai.com/edge/en/concepts/crews)
- Classic `tasks.yaml` example: `research_task: {description: "...{topic}...", expected_output: ..., agent: researcher}`; `reporting_task: {..., context: [research_task], output_file: report.md}`; Task fields also include `output_json`, `output_pydantic` (Pydantic model), `guardrail`, `async_execution`, `human_input` — [CrewAI: Tasks](https://docs.crewai.com/en/concepts/tasks)
- Agent fields: required `role`, `goal`, `backstory`; `llm`, `tools`, `function_calling_llm`, `max_iter` (default 20), `max_rpm`, `max_execution_time`, `max_retry_limit`, `allow_delegation` (default False), `cache`, `respect_context_window`, `reasoning`, `embedder`, `knowledge_sources`, `system_template`/`prompt_template`/`response_template`; `allow_code_execution` deprecated in favor of an external sandbox — [CrewAI: Agents](https://docs.crewai.com/en/concepts/agents)

**Google ADK Agent Config (YAML)**
- Experimental; supported in Python v1.11.0, Java v0.3.0, Go v0.3.0; Gemini models only; "Python is currently required" for setup. Fields: `agent_class`, `name`, `model`, `description`, `instruction`, `tools` (`- name: google_search` or a Python function path), `sub_agents` (`- config_path: code_tutor_agent.yaml`). Schema: `yaml-language-server` comment pointing at `AgentConfig.json` in adk-python. Create with `adk create --type=config my_agent` → `root_agent.yaml`; run with `adk web`/`adk run`/`adk api_server`; load in code via `config_agent_utils.from_config(...)` — [ADK: Agent Config](https://adk.dev/agents/config/)
- Schema reference: root `AgentConfig` is a union of `LlmAgentConfig`, `LoopAgentConfig`, `ParallelAgentConfig`, `SequentialAgentConfig`, `BaseAgentConfig`. `LlmAgentConfig`: `name`, `description`, `instruction`, `model` (inherited from ancestor if unset), `tools[]` (`name` + `args`; five reference kinds: built-in by bare name, tool instance by fqpath, tool class by fqpath with args, factory `def f(args: ToolArgsConfig) -> BaseTool`, function tool), `sub_agents[]` (`config_path` or `code`), `output_key`, `output_schema`, `input_schema`, `include_contents`, `generate_content_config`, `disallow_transfer_to_parent/peers`, and six callback lists (`before/after_{agent,model,tool}_callbacks`) each a `CodeConfig` (`name` fqpath + `args`) — [ADK: AgentConfig reference](https://adk.dev/api-reference/agentconfig/)
- Example:
  ```yaml
  # yaml-language-server: $schema=https://raw.githubusercontent.com/google/adk-python/main/src/google/adk/agents/config_schemas/AgentConfig.json
  agent_class: LlmAgent
  model: gemini-flash-latest
  name: root_agent
  description: Learning assistant for code and math.
  instruction: Delegate coding questions to code_tutor_agent and math to math_tutor_agent.
  tools:
    - name: google_search
  sub_agents:
    - config_path: code_tutor_agent.yaml
    - config_path: math_tutor_agent.yaml
  ```
  — [ADK: Agent Config](https://adk.dev/agents/config/) (schema URL form per the page's `yaml-language-server` comment; exact raw path not verified)

**OpenAI AgentKit / Agent Builder**
- Agent Builder exports a workflow as Agents SDK code (TypeScript or Python) via Code → Agents SDK; the official guide states the process "does not convert your workflow graph or guarantee that every behavior transfers unchanged"; triggers, tools, permissions, connected apps, auth need review; alternative path is recreating it as a ChatGPT Workspace Agent (Business/Enterprise/Edu). No deprecation date stated — [OpenAI: Migrate from Agent Builder](https://developers.openai.com/api/docs/guides/agent-builder/migrate-from-agent-builder)

**Windmill OpenFlow — general DAG IR with an `aiagent` node type**
- OpenFlow: "an open standard for Flows", DAG where each node is a computation step; Windmill is the reference implementation; source of truth is `openflow.openapi.yaml` in `windmill-labs/windmill`. Top-level: `summary`, `description`, `value` (FlowValue), `schema` (JSON Schema of inputs). FlowValue: `modules[]`, `failure_module`, `preprocessor_module`, `same_worker`, plus concurrency/caching/early-stop settings. Module: `id`, `value`, `summary`, `input_transforms`, `stop_after_if`, `stop_after_all_iters_if`, `skip_if`, `sleep`, `cache_ttl`, `timeout`, `mock`, `suspend` (`required_events`, `timeout`, `resume_form`, `user_auth_required`, `user_groups_required`, ...), `continue_on_error`, `retry` (`constant`/`exponential` + `retry_if`). Module types: `identity`, `rawscript` (`content`, `language`, `lock`, `tag`), `script` (`path`, `hash`; `hub/` prefix), `flow` (subflow by `path`), `forloopflow` (`iterator`, `parallel`, `parallelism`, `skip_failures`), `whileloopflow`, `branchone` (`branches[].expr`, `default`), `branchall`, `aiagent` (`input_transforms`, `tools` = scripts, flows, MCP servers, web search). Input transforms are `{type: static, value}` or `{type: javascript, expr}` over `results.<id>`, `flow_input` — [Windmill: OpenFlow](https://www.windmill.dev/docs/openflow)
- On-disk `flow.yaml` (from Windmill's own `write-flow` skill):
  ```yaml
  summary: Fetch data and notify
  value:
    modules:
      - id: fetch_data
        value:
          type: rawscript
          language: bun
          content: !inline fetch_data.ts
          input_transforms:
            url: { type: javascript, expr: flow_input.url }
      - id: per_item
        value:
          type: forloopflow
          iterator: { type: javascript, expr: results.fetch_data }
          modules:
            - id: notify
              value:
                type: script
                path: f/folder/send_email
                input_transforms:
                  to: { type: static, value: ops@example.com }
                  body: { type: javascript, expr: flow_input.iter.value }
  schema:
    type: object
    properties:
      url: { type: string }
  ```
  — [Windmill write-flow skill (tessl mirror)](https://tessl.io/registry/skills/github/windmill-labs/windmill/write-flow)

**A2A Agent Cards and MCP server.json (interoperability)**
- A2A latest released version 1.0.0 (previous 0.3.0, 0.2.6, 0.1.0); A2A joined the Agentic AI Foundation. AgentCard fields: `name`, `description`, `supportedInterfaces[]` (ordered; each `url`, `protocolBinding` ∈ {`JSONRPC`,`GRPC`,`HTTP+JSON`}, `protocolVersion`, optional `tenant`), `provider{url,organization}`, `version`, `documentationUrl`, `capabilities{streaming,pushNotifications,extensions,extendedAgentCard}`, `securitySchemes`, `securityRequirements`, `defaultInputModes`, `defaultOutputModes`, `skills[]` (`id`,`name`,`description`,`tags` required; `examples`,`inputModes`,`outputModes`,`securityRequirements`), `signatures[]` (JWS), `iconUrl`. Discovery at `https://{server_domain}/.well-known/agent-card.json` — [A2A Protocol Specification](https://a2a-protocol.org/latest/specification/)
- MCP `server.json`: `$schema` (`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`; page also links a `2025-09-29` schema), `name` (reverse-DNS, e.g. `io.github.example/weather-mcp`), `description`, `title`, `version`, `websiteUrl`, `repository{url,source,subfolder,id}`, `packages[]` (`registryType` ∈ npm/pypi/nuget/oci/cargo/mcpb, `identifier`, `version`, `runtimeHint` npx/uvx/dnx, `runtimeArguments`, `packageArguments`, `transport`, `environmentVariables[]{name,isRequired,isSecret,default,choices}`, `fileSha256`), `remotes[]` (`type` streamable-http/sse, `url` with `{variable}` templating, `headers`, `variables`), `_meta` — [MCP registry: generic server.json](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/generic-server-json.md)

### Inferences
- A "DAG of coding-agent sessions" spec would most naturally be: OpenFlow-style graph (modules with `id`, typed `input_transforms`, `retry`, `suspend`, subflow references) where each node's `value` is a gh-aw/Claude-subagent-like block (`engine`/`model`, `tools`, `skills`, `mcpServers`, `hooks`, `permissionMode`, `isolation`, `max-turns`, `network`, `safe-outputs`). gh-aw already demonstrates the compile-to-lock-file step and the field-by-field merge table that such a spec needs for inheritance; Claude Code's subagent frontmatter is the most complete per-node vocabulary found.
- gh-aw's choice to load the markdown body at runtime but embed frontmatter at compile time is a practical separation of "prompt (editable without recompile)" vs "runtime/security config (compiled, pinned)".
- The fact that gh-aw, Claude Code, Codex and ADK all converge on "Markdown/YAML file + schema URL comment + JSON-based inline alternative" suggests a de-facto convention: file-based spec for humans/git, JSON for programmatic injection.

### Gaps
- gh-aw: I could not verify whether markdown bodies of frontmatter imports are concatenated into the prompt (the imports page documents only `{{#runtime-import}}`); the documented `jobs:` merge behaviour is self-contradictory.
- ADK: `SequentialAgentConfig`/`LoopAgentConfig`/`ParallelAgentConfig` field lists (e.g. `max_iterations`) were not in the fetched portion of the reference; no official statement that AgentConfig.json is generated from Pydantic (though the Python SDK is Pydantic-based).
- Codex: `agents.*` (multi-agent/subagent roles) section of the config reference was not reached; no documented profile precedence order.
- No official date found for A2A 1.0.0 or Agent Skills spec version numbering.

---

## KQ2. What design choices make a YAML IR friendly to multi-language SDKs and UI editing?

### Takeaway
The mature examples (Pulumi, AWS CDK, CDKTF, Flyte, Airflow 3.1, Serverless Workflow 1.0, Logic Apps, Step Functions) converge on: a **versioned, language-neutral IR** (JSON/protobuf) with a **published schema** (JSON Schema or `.proto`), **SDKs generated from the schema rather than hand-written per language** (jsii, Pulumi package schema, flyteidl protobuf), **explicit defaults layering** (Airflow 3.1 `client_defaults`), **ids and `$ref`-style path references instead of positional structure**, and an **escape hatch** to the raw IR for things the typed SDK does not model.

### Cited Findings

**Code → IR compilation and multi-language parity**
- Pulumi Package Schema is a JSON document that "drives code generation for SDKs in each supported Pulumi language"; top-level `name`, `version` (semver), `resources`, `types`, `functions` keyed by type token, and per-language `language` sections (`nodejs`, `python`, `go`, `csharp`, `java`); each generator owns its extension and "A language that has no entry for a section does not read one"; several entries marked not supported by particular generators; metaschema from pulumi/pulumi v3.268.0 — [Pulumi Package Schema](https://www.pulumi.com/docs/iac/using-pulumi/pulumi-packages/schema/)
- `pulumi convert` converts programs from `--from` yaml (default), terraform, bicep, arm, kubernetes into `--language` typescript, python, csharp, go, java, yaml; `--generate-only`, `--strict` — [pulumi convert](https://www.pulumi.com/docs/iac/cli/commands/pulumi_convert/)
- Converter plugins produce PCL ("Pulumi configuration language", Pulumi's internal representation) over a gRPC service; name mapping is delegated to resource plugins; code generation then emits the target language; converters are one of five plugin types (resource, language, analyzer, tool, converter) — [Pulumi converters](https://www.pulumi.com/docs/iac/concepts/converters/index.md); [Pulumi plugins](https://www.pulumi.com/docs/iac/concepts/plugins/index.md)
- Pulumi YAML: `name`, `runtime: yaml`, `config` (`type`, `default`), `resources` (`type`, `properties`, `options.dependsOn`), `outputs`; `${resource.attribute}` interpolation; `fn::` builtins; a `compiler` runtime option lets an external program (e.g. `cue export`) emit YAML/JSON interpreted as a Pulumi YAML program — [Pulumi YAML](https://www.pulumi.com/docs/iac/languages-sdks/yaml/)
- AWS CDK: constructs form a tree (`node.id`, `node.path`, `node.uniqueId` generated from path+hash); apps are synthesized to CloudFormation templates; scope mechanism "works the same way in every language supported by the AWS CDK"; moving a construct to a different scope changes its generated ID so CloudFormation treats it as a new resource — [AWS CDK apps](https://docs.aws.amazon.com/cdk/v2/guide/apps.html)
- CDKTF: `cdktf synth` converts the app into JSON configuration that Terraform can use; one stack = one Terraform working directory; provider bindings generated from provider schemas; jsii creates bindings for other languages from a TypeScript assembly; "The Cloud Development Kit for Terraform is deprecated as of December 10, 2025" (v0.21.x latest) — [CDKTF architecture](https://developer.hashicorp.com/terraform/cdktf/concepts/cdktf-architecture)
- Flyte: flyteidl holds "the specification of the Flyte language using protobuf messages", a gRPC backend API and Swagger REST spec, with generated clients in multiple languages — [flyteidl README](https://github.com/flyteorg/flyte/blob/master/flyteidl/README.md); `pyflyte` is "code-aware ... responsible for the serialization (compilation) step": it scans user code for tasks/workflows/launch plans and compiles them to protobuf; a task serializes as a `TaskTemplate`; `pyflyte package` → `flyte-package.tgz`; `flytectl register files` sends protobufs to FlyteAdmin; `pyflyte register` does both but does not write protobufs to disk — [Flyte CLI design](https://flyte.readthedocs.io/en/latest/api/flytekit/design/clis.html); [Flyte: Registering workflows](https://flyte.readthedocs.io/en/latest/flyte_fundamentals/registering_workflows.html)
- Temporal: a Workflow Definition is code (Go, Java, PHP, Python, TypeScript, .NET, Rust, Ruby); must be deterministic for replay; no declarative/serialized definition language is described; versioning via Worker Versioning (recommended) or `Patched`/`GetVersion`; experimental pre-2025 Worker Versioning removed from server March 2026 — [Temporal: Workflow Definition](https://docs.temporal.io/workflow-definition)
- Airflow serialization: DagFileProcessor parses DAG files and stores them as JSON (`SerializedDagModel`); webserver and scheduler read serialized DAGs, not files; required since 2.0. Airflow 3.1+: serialized DAGs carry a `__version` field (example `3`) and optional `client_defaults`; defaults apply in order schema defaults → client defaults → DAG `default_args` → mapped-operator partial args → explicit task values, enabling Task SDK and server components to upgrade independently; schema at `.../schemas/dag-serialization/v2.json` and execution API `2025-05-20.json` — [Airflow DAG serialization](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/dag-serialization.html)
- Pydantic: `model_json_schema()` / `TypeAdapter.json_schema()` emit JSON Schema Draft 2020-12 and OpenAPI 3.1.0; `mode='validation'|'serialization'`; nested models go into `$defs` with `$ref` (inlined when metadata differs); `json_schema_extra`, `WithJsonSchema`, `SkipJsonSchema`, custom `GenerateJsonSchema`; `ref_template` for `#/components/schemas/{model}` — [Pydantic JSON Schema](https://pydantic.dev/docs/validation/latest/concepts/json_schema/)

**Declarative workflow DSLs with published schemas**
- Serverless Workflow / "Open Workflow Specification": DSL 1.0.3; `document: {dsl, namespace, name, version}` + `do:` task list; task types in examples: call, run, emit, for, listen, fork, switch, try, raise, wait, set, do; `then: exit` / `then: <task>` directives; `${ .pet.id }` expressions with `$error`, `$context`; JSON Schema published for 1.0.3; SDKs for .NET, Go, Java, PHP, Python, Rust, TypeScript; runtimes Synapse, Apache EventMesh Workflow, Apache KIE SonataFlow, Lemline, Quarkus Flow, Zigflow; CNCF-governed — [open-workflow-specification.org](https://open-workflow-specification.org/). CNCF lists "Open Workflow Specification" as a Sandbox project accepted July 14, 2020 at the heart of the Serverless Workflow project; a repo issue proposes renaming to "CNCF Workflow Specification"; naming is not settled — [CNCF project page](https://www.cncf.io/projects/open-workflow-specification/); [spec issue #1127](https://github.com/serverlessworkflow/specification/issues/1127)
- Amazon States Language: JSON with `StartAt`, `States`, state `Type` (Task/Choice/Parallel/Map/Pass/Wait/Succeed/Fail), `QueryLanguage: JSONata` with `{% ... %}` expressions and `Assign`, `Next`/`End`; files saved as `*.asl.json`; validator Statelint — [Step Functions ASL](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-amazon-states-language.html)
- Azure Logic Apps Workflow Definition Language: `definition: {$schema, contentVersion (default "1.0.0.0"), parameters, triggers, actions, outputs, staticResults}`; schema `https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json`; actions ordered by `runAfter`; `@`-prefixed expressions with `@{...}` interpolation; limits 250 actions/10 triggers/50 parameters; multiple triggers only via code, not designer; `staticResults` provides mock outputs per action — [Logic Apps WDL schema reference](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-workflow-definition-language) (page dated 2025-10-06)
- Argo Workflows: `WorkflowTemplate` (v2.4+) referenced via `templateRef {name, template}` from `steps`/`dag` templates; `workflowTemplateRef` (v2.9+) lets a Workflow run a template's whole spec with arguments merged; `ClusterWorkflowTemplate` (v2.8+) adds `clusterScope: true`; global params `{{workflow.parameters.x}}` vs local `{{inputs.parameters.x}}`; template types container/script/dag/steps/resource/suspend; `dag.tasks[].dependencies` — [Argo WorkflowTemplates](https://argo-workflows.readthedocs.io/en/latest/workflow-templates/); [Argo ClusterWorkflowTemplates](https://argo-workflows.readthedocs.io/en/latest/cluster-workflow-templates/)
- Orchestra (getorchestra.io): "each pipeline is in fact YAML"; schema requires `version: "v1"`, `name` ≤100 chars; reference generated from a public JSON Schema; GitBridge stores `pipeline.yml` in Git; `orchestra-cli validate` checks YAML against the API schema; `orchestra import` creates a new pipeline each call (duplicates) — [Orchestra pipeline schema](https://docs.getorchestra.io/docs/core-concepts/pipelines/schema.md); [Orchestra CLI](https://docs.getorchestra.io/docs/git-control-and-ci-cd/orchestra-cli.md)
- Claude Code `plugin.json` has a `$schema` field "for editor autocomplete" that the runtime ignores; unknown top-level keys are stripped with a warning while unknown keys inside strict sub-objects (`userConfig`, `channels`, `lspServers`, `monitors`) fail validation — [Claude Code: Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference)
- Dify workflow DSL (YAML): import checks DSL version and warns when older than the platform; SaaS always runs latest; self-hosted may need upgrading to import newer files; a third-party skill reports App DSL "0.7.0" in Dify 1.16.x vs "0.6.0" in 1.15.x (unverified against official release notes); community tool `difyctl` lints/diffs/canonically formats DSL because raw git diffs are hard to review — [Dify app management](https://docs.dify.ai/en/use-dify/workspace/app-management); [difyctl](https://github.com/JSLEEKR/difyctl); [dify-workflow-dsl-skill](https://github.com/yzmw123/dify-workflow-dsl-skill)

### Inferences
- Design checklist distilled from the sources: (1) a `version`/`dsl`/`__version`/`contentVersion` field at the document root (Serverless Workflow, Airflow 3.1, Logic Apps, Orchestra, Dify all have one); (2) publish a JSON Schema and expose it via `$schema`/`yaml-language-server` comment (Claude plugin.json, ADK, Codex `#:schema`, Logic Apps, Orchestra); (3) stable string `id`s per node with expression references (`results.<id>`, `{{inputs.parameters.x}}`, `runAfter`) rather than positional edges, so UI drag/drop and code refactors do not invalidate references; (4) defaults as explicit layers with documented precedence (Airflow `client_defaults`, DAG Factory, Kestra 1.x pluginDefaults); (5) keep the graph (nodes/edges/transforms) separate from runtime/security config (gh-aw frontmatter vs body; Prefect flow code vs `prefect.yaml`; Windmill `flow.yaml` vs inline scripts and `.lock`); (6) generate SDKs from the schema (Pulumi schema, jsii, protobuf) rather than hand-porting; (7) lenient-unknown at the top level, strict inside typed sub-objects (Claude plugin.json) to allow forward-compatible extensions.
- Protobuf (Flyte) gives strongest cross-language typing but weakest hand-editability; JSON Schema (Pulumi, Pydantic, Serverless Workflow) is the pragmatic middle; plain YAML without schema (Kestra pre-2.0 defaults, Dify) shows the most migration pain.
- CDKTF's deprecation (Dec 2025) is a cautionary data point: a code-first layer over a declarative IR owned by someone else is costly to maintain.

### Gaps
- Could not find a 2024–2026 first-party blog post from Pulumi or AWS specifically on "code→IR design lessons"; findings above are from reference docs.
- Serverless Workflow 1.0 release date and the official status of the rename were not confirmed.
- No source found quantifying parity gaps between Pulumi language generators beyond "not supported by the X code generator" markers.

---

## KQ3. How do Windmill OpenFlow, Kestra, Dagster components and Airflow DAG Factory structure inheritance/defaults and sub-flows?

### Takeaway
Four different answers: **Windmill** has no defaults layer — composition is by reference (`type: flow` subflow by `path`, `type: script` by `path`/`hash`, `!inline` files) and per-module settings; **Kestra** had a three-level `pluginDefaults` precedence (task > flow > namespace > global, with `forced`) and removed it in 2.0 in favour of Policies; **Dagster** uses Pydantic-schema components instantiated from `defs.yaml` with Jinja templating, `template_vars_module` and `post_processing` overrides; **DAG Factory** has a four-level precedence (DAG config > file `default` block > `default_args_config_dict` > hierarchical `defaults.yml`, nearest file wins).

### Cited Findings

**Windmill**
- Subflow: `{type: flow, path: <flow path>}` runs another flow; scripts referenced by `path` (optionally pinned by `hash`); Hub scripts with `hub/` prefix; `forloopflow`/`whileloopflow`/`branchone`/`branchall` nest `modules` inline — [Windmill: OpenFlow](https://www.windmill.dev/docs/openflow)
- On disk: a flow is a folder ending `.flow` (or `__flow` depending on workspace `nonDottedPaths`), containing `flow.yaml` and one file per inline script (`content: !inline fetch_data.ts`); `wmill flow new <path>` scaffolds; `wmill generate-metadata` regenerates stale `.lock` files and refreshes hashes in `wmill-lock.yaml`; scripts are split into code file + `.script.yaml` metadata + lockfile — [Windmill: Local development](https://www.windmill.dev/docs/advanced/local_development); [Windmill write-flow skill](https://tessl.io/registry/skills/github/windmill-labs/windmill/write-flow)
- Naming inconsistency: CLI scaffold output uses `step_1.inline_script.ts`, while the write-flow skill says inline files should NOT include `.inline_script.` — [Windmill local development (search summary)](https://www.windmill.dev/docs/advanced/local_development.md); [write-flow skill](https://tessl.io/registry/skills/github/windmill-labs/windmill/write-flow)
- Flow input schema is a JSON Schema under `schema:`; resource inputs use `type: object` + `format: resource-<type>` — [Windmill write-flow skill](https://tessl.io/registry/skills/github/windmill-labs/windmill/write-flow)

**Kestra**
- Subflow task `io.kestra.plugin.core.flow.Subflow` with `namespace`, `flowId`, `inputs`, `wait` (default true), `transmitFailed`, `inheritLabels` (default false), `revision` (defaults latest), `scheduleDate`; outputs read as `outputs.<taskId>.outputs.<name>`; recursive flows not supported; nested dotted input ids (`users.firstName`) validate extracted JSON fields; docs versions listed: 2.0 (latest), 1.3, 1.2, 1.1, 1.0 — [Kestra: Subflows](https://kestra.io/docs/workflow-components/subflows)
- Example:
  ```yaml
  id: parent_flow
  namespace: company.team
  tasks:
    - id: call_child
      type: io.kestra.plugin.core.flow.Subflow
      namespace: company.team
      flowId: child_flow
      inputs:
        url: "{{ inputs.url }}"
      wait: true
      transmitFailed: true
  ```
  — [Kestra: Subflows](https://kestra.io/docs/workflow-components/subflows)
- `pluginDefaults` (1.x): flow-level block of `{type, values, forced}`; namespace-level (EE) via UI/API; global under `kestra.plugins.defaults`. `forced: false` fills only unset properties, `forced: true` always wins (flow-level never supported `forced`). Precedence non-forced: task > flow > namespace > global. "The `pluginDefaults` keyword is removed in Kestra 2.0 in all editions and at all scopes"; "Flows that contain a `pluginDefaults` block fail to parse after upgrading to 2.0.0". Replacements: global → `kestra.policies`; namespace defaults → namespace-scoped Policies (auto-migrated); flow-level → inlined task values (OSS) or `policyRefs:` REFERENCE Policy (EE); `forced: true` → `override: true` on an Add rule — [Kestra: Plugin defaults](https://kestra.io/docs/workflow-components/plugin-defaults)
- Kestra's official Python SDK (`kestrapy`) manages flows by sending YAML text to the API (`create_flow`, `update_flow` with full YAML); the PyPI package `kestra` is an unofficial helper for logging/outputs inside tasks; no code-first flow builder found — [Kestra Python SDK](https://kestra.io/docs/api-reference/kestra-sdk/python-sdk); [pypi kestra](https://pypi.org/project/kestra/)

**Dagster Components (docs "Latest (1.13.26)")**
- `defs.yaml` has two top-level fields `type` (component class) and `attributes`; multiple instances per file separated by `---`; Jinja templating with `{{ env.FIVETRAN_API_KEY }}`; `dg scaffold defs <component> <path>` (always under `src/<project>/defs/`), `--format python` emits `component.py` instead; `dg list components`; `dg dev` Docs tab shows generated docs and sample YAML — [Dagster: Adding component definitions](https://dagster.io/docs/guides/build/components/building-pipelines-with-components/adding-component-definitions)
  ```yaml
  type: dagster_fivetran.FivetranAccountComponent
  attributes:
    workspace:
      account_id: test_account
      api_key: "{{ env.FIVETRAN_API_KEY }}"
  ```
- A component inherits `dg.Component` and `dg.Resolvable`; adding `dg.Model` makes it a Pydantic model and gives it a YAML interface; without `Model`, a Pythonic `__init__`/`@dataclass` interface; annotated fields define the generated YAML schema (helpers `ResolvedAssetSpec`, `ResolvedAssetKey`, `AssetAttributesModel`); `build_defs(context: ComponentLoadContext) -> Definitions`; `dg scaffold component <Name>` (`--no-model` for Pythonic) — [Dagster: Creating and registering a component](https://dagster.io/docs/guides/build/components/creating-new-components/creating-and-registering-a-component)
- `template_vars_module` points `defs.yaml` at a module of `@dg.template_var` functions (relative dotted path recommended; functions may take `ComponentLoadContext`); post-processing: one guide uses a `post_process` field, another uses top-level `post_processing: {assets: [{target: "*", attributes: {group_name: team_a}}]}` — docs disagree on the key name; post-processing currently only supported for assets — [Dagster: Using template variables](https://docs.dagster.io/guides/build/components/building-pipelines-with-components/using-template-variables); [Dagster: Post-processing components](https://docs.dagster.io/guides/build/components/building-pipelines-with-components/post-processing-components)
- Components overview describes "a toolkit also supports building YAML domain-specific languages (DSLs) for components" and a class-based interface that "builds Dagster definitions dynamically from arbitrary data, such as third-party integration config files" — [Dagster: Components](https://dagster.io/docs/guides/build/components)

**Airflow DAG Factory (Astronomer)**
- Version 1.0 released with breaking changes (migration guide); compatible with Airflow 3 — [DAG Factory docs](https://astronomer.github.io/dag-factory/latest/)
- Four ways to set defaults: `default_args` in one DAG; top-level `default` block for all DAGs in the file; `default_args_config_dict` on `DAGFactory`; `defaults.yml`/`defaults.yaml` via `defaults_config_path` (the last two cannot be combined). `defaults.yml` in the same directory is auto-used; since 0.22.0 `default_args` may live in `defaults.yml` ("DagFactory will apply these to all generated DAGs"); DAG-level args (`schedule`, `catchup`) at the root of `defaults.yml`. With `defaults_config_path="a"`, `load_yaml_dags("a/b/c/some_dags.yml", ...)` merges `a/b/c/`, `a/b/`, `a/` with the nearest file winning. Precedence (highest first): DAG config → file `default` block → `default_args_config_dict` → `defaults.yml` hierarchy — [DAG Factory: Defaults](https://astronomer.github.io/dag-factory/latest/configuration/defaults/)
  ```yaml
  default:
    default_args: { start_date: '2024-01-01' }
    schedule: 0 0 * * *
    catchup: false
  etl:
    tasks:
      - task_id: extract
        operator: airflow.operators.bash.BashOperator
        bash_command: "echo extract"
      - task_id: transform
        operator: airflow.operators.bash.BashOperator
        bash_command: "echo transform"
        dependencies: [extract]
  ```

**Related: Prefect, Argo, Airflow bundles**
- Prefect `prefect.yaml`: `name`, `prefect-version`, `build`/`push`/`pull` step lists (`pull` the only required action; omitted `pull` gets auto `set_working_directory`, `pull: []` disables), `deployments[]` (`name`, `version`, `tags`, `schedules`, `triggers`, `entrypoint: flows/hello.py:my_flow`, `parameters`, `enforce_parameter_schema`, `work_pool{name, work_queue_name, job_variables}`); per-deployment `build`/`push`/`pull` override top-level; a `definitions` section holds YAML anchors reused via `*name` and `<<: *name` merge; templating `{{ step-id.field }}`, `{{ prefect.blocks.<type>.<slug> }}`, `{{ prefect.variables.<name> }}`, `{{ $ENV }}`; flow code stays in Python — [Prefect: prefect.yaml](https://docs.prefect.io/v3/how-to-guides/deployments/prefect-yaml)
- Argo: reuse by `templateRef`/`workflowTemplateRef`/`clusterScope` (see KQ2) — [Argo WorkflowTemplates](https://argo-workflows.readthedocs.io/en/latest/workflow-templates/)
- Airflow 3 (docs 3.3.2) Dag bundles: `LocalDagBundle` (no versioning), `GitDagBundle` (versioned by commit), `S3DagBundle`, `GCSDagBundle` (no versioning); configured in `[dag_processor] dag_bundle_config_list` as `{name, classpath, kwargs}`; each Dag run records the bundle version it was created with; `rerun_with_latest_version` precedence: API `run_on_latest_version` → Dag-level param → `[core] rerun_with_latest_version` → per-path fallback (False for clear/rerun, True for backfills); `disable_bundle_versioning` turns tracking off — [Airflow: Dag bundles](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/dag-bundles.html)

### Inferences
- Reference-by-path (Windmill `path`, Argo `templateRef`, Kestra `flowId`+`revision`, ADK `config_path`) is the dominant sub-flow mechanism; YAML anchors appear only in Prefect's `definitions`. None of the four uses JSON-Schema `$ref` for inheritance.
- Kestra's 2.0 removal of `pluginDefaults` and gh-aw's per-field merge table are both evidence that implicit multi-level defaults become a support burden; explicit, named policies with `override` flags or compile-time merge tables are the trend.
- Dagster's pattern (Python class = schema, YAML = instance, Jinja for env/templating, post-processing for cross-cutting overrides) is the closest to "code-first SDK with a YAML surface that a UI can also write".

### Gaps
- Dagster: no page found stating a `dg check yaml` command or a published JSON Schema for `defs.yaml` editor validation; the `post_process` vs `post_processing` key discrepancy is unresolved.
- Kestra 2.0 Policies syntax (`policyRefs`, Add rules) not fetched in detail.
- Windmill `wmill.yaml` include/exclude syntax and `nonDottedPaths` semantics not documented in the fetched pages.

---

## KQ4. What are real-world lessons on round-tripping between code and UI-edited definitions (conflicts, ownership, lock files)?

### Takeaway
Tools that succeed at round-tripping either (a) make a serialized file the single source of truth with the UI as a thin editor over it (Windmill `wmill dev` round-trips page edits to `flow.yaml`; Logic Apps designer ↔ JSON; Dify/n8n export JSON/YAML into git), or (b) refuse to round-trip and keep code canonical (LangGraph Studio visualizes and debugs but does not claim structural editing; Airflow has no UI DAG editing and versions by bundle commit; Prefect keeps flows in Python and only deployments in YAML; OpenAI Agent Builder export explicitly "does not convert your workflow graph"). Ownership is handled by separating generated/managed files (lockfiles, `.lock.yml`, hashes) from user-owned files.

### Cited Findings
- Windmill: `wmill dev` live-reloads on disk edits and "edits in the page round-trip back to the local files (`flow.yaml`, inline scripts, `app.yaml`, etc.)"; lockfile "is not meant to be edited manually", generated by Windmill for UI edits and by `generate-metadata` locally, and locally the lockfile takes precedence over versions pinned in the script; manual edits to `summary`/`description` are preserved by `generate-metadata`; `wmill init` creates managed files that are "refreshed on every run and are not meant to be edited" vs user-owned files "created once, never overwritten"; recommended collaboration = workspace forks + Git Sync, or a Git Promotion workflow; `wmill sync pull --skip-variables --skip-secrets --skip-resources` recommended with Git; no explicit conflict-resolution procedure documented — [Windmill: Local development](https://www.windmill.dev/docs/advanced/local_development)
- gh-aw: compiler writes `.lock.yml` only when content changed; repos instruct never to merge Dependabot PRs that modify lock files directly — update the source `.md` and rerun `gh aw compile --dependabot`; lock files carry a metadata header (compiler version, strict flag, agent id) and a manifest of secrets and pinned action SHAs; `.github/aw/actions-lock.json` should be committed so contributors share pins — [gh-aw Compilation process](https://github.github.com/gh-aw/reference/compilation-process/); [gh-aw compile (search summary)](https://pkg.go.dev/github.com/github/gh-aw@v0.82.4/pkg/workflow)
- LangGraph/LangSmith Studio: "a specialized agent IDE that enables visualization, interaction, and debugging of agentic systems that implement the Agent Server API protocol"; capabilities: visualize graph architecture, run/interact, manage assistants/threads, iterate on prompts, run experiments, manage memory, time-travel debugging, deploy; the official page does not mention visual add/remove of nodes — [LangSmith Studio](https://docs.langchain.com/langsmith/studio). The launch post frames Studio as complementing code, "even if development still primarily happens in code"; third-party tutorials claiming drag-and-drop graph editing were not confirmed against official docs — [LangChain blog: LangGraph Studio](https://blog.langchain.com/langgraph-studio-the-first-agent-ide)
- OpenAI Agent Builder → code: export yields Agents SDK code but "does not convert your workflow graph or guarantee that every behavior transfers unchanged"; deterministic workflows "may not migrate faithfully" to a workspace agent — [OpenAI: Migrate from Agent Builder](https://developers.openai.com/api/docs/guides/agent-builder/migrate-from-agent-builder)
- Dify: export includes app config, workflow orchestration and node settings, model params/prompts, KB connections but not KB content or third-party API keys; secret env vars prompt on export; import checks DSL version and warns if older; app deletion is permanent so keep DSL backups — [Dify app management](https://docs.dify.ai/en/use-dify/workspace/app-management)
- n8n: Git-based source control on Business/Enterprise plans; each environment backed by a Git branch; owners/admins push and pull; project admins push only; docs index links "push and pull", "compare versions with workflow diffs", "moving work between environments" subpages — [n8n: Source control and environments](https://docs.n8n.io/source-control-environments/)
- Orchestra: recommends building in the UI editor first (it shows the schema per task type), then downloading the `.yml` to commit; CLI `validate` checks YAML against the API schema; `import` creates duplicates on repeat, use PUT to update — [Orchestra CLI](https://docs.getorchestra.io/docs/git-control-and-ci-cd/orchestra-cli.md); [DataOps Leadership: GitBridge](https://dataopsleadership.substack.com/p/a-declarative-orchestration-framework)
- Logic Apps: workflow has "an underlying JSON definition"; the designer's expression editor writes expressions back into the JSON (`"searchQuery": "Azure @{concat(...)}"`); multiple triggers "only with the Workflow Definition Language, not visually through the workflow designer" — [Logic Apps WDL schema reference](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-workflow-definition-language)
- Airflow 3: bundle versioning means "a run can use the same code throughout, even if the Dag changes mid-run"; `GitDagBundle` records the commit per Dag run so reruns reproduce the exact code; no UI DAG editing described — [Airflow: Dag bundles](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/dag-bundles.html)
- Airflow 3.1 versioned serialization contract (`__version`, `client_defaults`) exists so "Task SDKs and server components be deployed and upgraded more independently" — [Airflow DAG serialization](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/dag-serialization.html)
- Claude Code plugins: `${CLAUDE_PLUGIN_ROOT}` changes on plugin update ("don't write state there"); `${CLAUDE_PLUGIN_DATA}` persists across updates; user `enabledPlugins` entries persist across updates so changing `defaultEnabled` later does not affect existing users — [Claude Code: Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference)
- CDK escape hatches: reach the L1 via `bucket.node.defaultChild as s3.CfnBucket`, then `addPropertyOverride('VersioningConfiguration.Status','Enabled')`, `addOverride('UpdateReplacePolicy','Retain')`, `addDeletionOverride(...)`; overrides apply at synthesis and bypass L2 validation; no public API wraps an arbitrary L1 into an L2 — use `fromXxxAttributes` imports instead; `cdk synth` writes `cdk.out/manifest.json` + `<Stack>.template.json` — NOTE: this fetch returned only the page title; the summary is the fetch tool's own recollection, not verified page text — [AWS CDK: Escape hatches (unverified)](https://docs.aws.amazon.com/cdk/v2/guide/cfn_layer.html)

### Inferences
- The recurring ownership pattern: three file classes — (1) human-authored source (`.md`, `flow.yaml`, `defs.yaml`), (2) machine-generated artifacts that must be committed but never hand-edited (`.lock.yml`, `*.lock`, `wmill-lock.yaml`, `actions-lock.json`, `cdk.out`), (3) secrets/variables deliberately excluded from sync. Tools that document which class each file is in (Windmill, gh-aw) report fewer conflict issues than tools that export one monolithic blob (Dify, early n8n).
- True bidirectional structural editing is rare; the pragmatic design is "UI writes the same canonical file the code writes", with idempotent, content-hash-gated regeneration (gh-aw writes lock only on change; Windmill `generate-metadata --dry-run` reports what is stale) and canonical formatting to keep diffs reviewable (difyctl exists precisely because Dify's export is not canonical).
- For a coding-agent DAG, the prompt/instructions body should be runtime-loaded (gh-aw style) so UI prompt edits do not force recompilation, while graph structure and security config are compiled and pinned.

### Gaps
- n8n's actual conflict behaviour on pull (both sides changed) and file naming were not in the fetched index page.
- No primary source found describing a lock-file or three-way-merge strategy for UI-edited workflow definitions in any agent framework; Windmill's docs explicitly give no conflict-resolution procedure.
- The CDK escape-hatch page fetch failed to return body text; details above are plausible but unverified for this report.

---

## Supplementary: typed artifact / schema conventions between steps (topic (d))

### Takeaway
Between-step typing in the surveyed systems is done with JSON Schema (Windmill flow `schema`, Pydantic-generated schemas in CrewAI `output_pydantic`/ADK `output_schema`, Pulumi package schema), protobuf `TaskTemplate`/literal types (Flyte), and transport envelopes (CloudEvents, OCI artifacts) rather than Arrow; "artifact manifests" in the OCI sense are now standardized (`artifactType`, empty config descriptor, `subject` referrers).

### Cited Findings
- Windmill flows declare inputs as JSON Schema (`schema: {type: object, properties: ...}`) and step inputs via `input_transforms` typed static/javascript — [Windmill: OpenFlow](https://www.windmill.dev/docs/openflow)
- ADK `LlmAgentConfig` exposes `output_schema`, `input_schema`, `output_key` for structured hand-off between agents — [ADK: AgentConfig reference](https://adk.dev/api-reference/agentconfig/)
- CrewAI tasks support `output_json` / `output_pydantic` (Pydantic model) and `context` (upstream task outputs) — [CrewAI: Tasks](https://docs.crewai.com/en/concepts/tasks)
- Pydantic JSON Schema output is Draft 2020-12 / OpenAPI 3.1.0 with `$defs`/`$ref` — [Pydantic JSON Schema](https://pydantic.dev/docs/validation/latest/concepts/json_schema/)
- Flyte serializes a task as a `TaskTemplate` protobuf as defined in FlyteIDL — [Flyte CLI design](https://flyte.readthedocs.io/en/latest/api/flytekit/design/clis.html)
- OCI image manifest for artifacts: `artifactType` "MUST be set when `config.mediaType` is set to the empty value"; empty descriptor `application/vnd.oci.empty.v1+json` (`{}`, size 2, digest `sha256:44136fa3...`); arbitrary content in `layers` with custom media types; `subject` creates a weak association used by the referrers API; three-pattern "Guidelines for Artifact Usage" (no content / content only / JSON metadata + content); tooling should fall back to `config.mediaType` for older artifacts — [OCI image-spec manifest](https://github.com/opencontainers/image-spec/blob/main/manifest.md)
- CloudEvents: current working draft 1.0.3-wip; `specversion` MUST be `1.0`; required `id`, `source`, `specversion`, `type`; optional `datacontenttype`, `dataschema`, `subject`, `time`; all implementations MUST support the JSON format; HTTP, AMQP, MQTT, SMTP, Kafka, NATS named as example delivery protocols — [CloudEvents spec](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)
- Serverless Workflow 1.0.3 `emit`/`listen` tasks are event-based and the DSL uses CloudEvents-style event payloads in examples — [open-workflow-specification.org](https://open-workflow-specification.org/)
- Airflow 3.1 serialized DAGs are schema-versioned (`__version`, `dag-serialization/v2.json`) — [Airflow DAG serialization](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/dag-serialization.html)
- MCP `server.json` environment variables carry `isRequired`/`isSecret`/`default`/`choices` typing — [MCP registry: generic server.json](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/generic-server-json.md)

### Inferences
- For a coding-agent DAG, the natural artifact contract is: JSON Schema per edge (generated from Pydantic/TypeScript types in SDKs), with large or binary outputs referenced by digest (OCI-style `artifactType` + `subject`) and events/notifications wrapped in CloudEvents.

### Gaps
- No evidence found of Arrow being used as an inter-step contract in any of the surveyed agent or workflow specs; it was not searched further.
- OCI spec version/date that introduced `artifactType` (1.1.0) was not confirmed from the fetched page.
