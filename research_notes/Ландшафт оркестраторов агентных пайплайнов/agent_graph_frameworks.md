# Agent-graph / multi-agent frameworks: durability, HITL, state & artifact passing (as of Oct 2026)

Scope: LangGraph (+ LangSmith Deployment, ex-LangGraph Platform), CrewAI (Crews + Flows, AMP), OpenAI Agents SDK (+ Temporal), Google ADK 2.0, Microsoft Agent Framework (MAF, successor of AutoGen + Semantic Kernel), Pydantic AI / pydantic-graph, Mastra, Inngest AgentKit, Anthropic Managed Agents / Claude Agent SDK, smolagents (brief).

Version snapshot (all from primary docs unless noted): LangGraph 1.2.x (1.2.0 on 2026-05-12, 1.2.6 on 2026-06-18 per a third-party release tracker; `interrupt` response schemas need `langgraph>=1.2.12` per official docs); CrewAI docs versioned up to v1.15.27, `@human_feedback` requires >=1.8.0; Microsoft Agent Framework 1.0 GA early April 2026 (sources disagree Apr 2 vs Apr 3), Python 1.13.0 changed checkpoint semantics; Google ADK Python 2.0 GA 2026-05-19, ADK Go 2.0 GA 2026-06-30, Agent Config YAML since Python v1.11.0; Mastra v1 (docs reference `@mastra/core` 1.51.0); Claude Managed Agents public beta since 2026-04-08 with beta header `managed-agents-2026-04-01`, multiagent type `multiagent_20261001`; Claude Agent SDK TS v0.3.2xx / Python v0.2.127+; Temporal OpenAI Agents integration "Generally Available", requires Temporal 1.34+; smolagents docs v1.26.0.

---

## KQ1. How does each framework describe a graph/flow in code (nodes, edges, conditional edges, subgraphs, decorators), and does a declarative (YAML/JSON) form or visual editor exist?

### Takeaway
Three families exist: (1) explicit graph builders with typed state (LangGraph `StateGraph`, MAF `WorkflowBuilder`, ADK 2.0 graphs, pydantic-graph `BaseNode`/`GraphBuilder`, Mastra `createWorkflow().then()...`); (2) decorator-driven event flows (CrewAI Flows `@start/@listen/@router`, MAF functional `@workflow/@step`, ADK `@node`); (3) "no graph at all" router loops where the LLM or a router function picks the next agent (Inngest AgentKit networks, OpenAI Agents SDK handoffs, Claude Agent SDK / Managed Agents subagents, smolagents `managed_agents`). Declarative YAML exists for *agent definitions* (CrewAI agents.yaml/tasks.yaml + crew.jsonc, ADK Agent Config YAML, MAF declarative workflow YAML, LangGraph `langgraph.json` for deployment only); true visual graph *editing* is rare — LangGraph Studio visualizes/debugs but does not edit; CrewAI Crew Studio is a no-code crew builder; OpenAI's Agent Builder visual canvas is being shut down 2026-11-30.

### Cited Findings

**LangGraph (code)**
- `StateGraph` "is parameterized by a user defined `State` object"; "The main documented way to specify the schema of a graph is by using a TypedDict"; reducers per key: `messages: Annotated[list[AnyMessage], add_messages]`; "Each key in the `State` has its own independent reducer function." — [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- Edges: `graph.add_edge("node_a", "node_b")`; conditional: `graph.add_conditional_edges("node_a", routing_function, {True: "node_b", False: "node_c"})`; `graph.add_edge(START, "node_a")`, `graph.add_edge("node_a", END)`; dynamic routing from inside a node: `return Command(update={"foo": "bar"}, goto="my_other_node")`; map-reduce: "LangGraph supports returning `Send` objects from conditional edges"; subgraph navigation via `Command(graph=Command.PARENT)`; per-node cache: `builder.add_node("expensive_node", expensive_node, cache_policy=CachePolicy(ttl=3))`; `@task` from `langgraph.func` with results "checkpointed when the graph uses a checkpointer"; `@entrypoint` compiles to a single node (functional API). — [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- Deployment manifest `langgraph.json`: `{"dependencies": ["."], "graphs": {"support_agent": "./my_agent/support.py:agent", "research_agent": "./my_agent/research.py:agent"}, "env": "./.env"}`; "Each key is a graph ID. Each value points to a compiled graph or to a function that defines one"; each graph gets a default assistant selected via `assistant_id`; supports Google ADK apps through the `deployments-wrap-sdk` package. — [LangSmith application structure](https://docs.langchain.com/langsmith/application-structure)
- LangGraph Studio: "a specialized agent IDE that enables visualization, interaction, and debugging"; can "Visualize your graph architecture", "Debug agent state via" time travel, manage assistants/threads; Graph mode vs Chat mode (chat mode only for `MessagesState`); the page describes visualization/debugging only, not visual no-code editing of the graph. — [LangSmith Studio](https://docs.langchain.com/langsmith/studio)

**CrewAI (code + YAML)**
- Flow with typed Pydantic state: `class StateExampleFlow(Flow[ExampleState]): @start() def first_method(self): ... @listen(first_method) def second_method(self): ...`; control flow: `@listen(or_(a, b))` fires on any, `@listen(and_(a, b))` fires when all; `@router(start_method) def second_method(self) -> Literal["success", "failed"]` then `@listen("success")`; `flow.plot("my_flow_plot")` writes an HTML diagram, also `crewai flow plot`. — [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)
- A Crew is called inside a flow step: `result = PoemCrew().crew().kickoff(inputs={...}); self.state.poem = result.raw`; generated crews keep agents/tasks in `config/agents.yaml` and `config/tasks.yaml`; JSON-first crews load from `crew.jsonc` via `from crewai.project import load_crew; crew, default_inputs = load_crew(Path(...)/"crew.jsonc")`. — [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)
- `@start()` "only runs once"; routers are exempt from the fire-once rule and can re-execute per loop iteration; "a `@start()` method cannot self-loop". — [CrewAI human feedback in flows](https://docs.crewai.com/en/learn/human-feedback-in-flows)
- CrewAI AMP: Crew Studio = "Create and customize crews using a no-code/low-code interface"; deployed crews via REST API. — [CrewAI AMP intro](https://docs-platform.crewai.com/platform/en/introduction)

**OpenAI Agents SDK**
- No graph object; the SDK primitives are agent definitions, tools, handoffs and guardrails (a third-party analysis of Agent Builder says it generates "an agent definition, a list of tools, handoff rules between agents, and a set of input and output guardrails"). — [osher.com.au on AgentKit](https://osher.com.au/blog/openai-agentkit/)
- OpenAI's visual Agent Builder: "a visual canvas for building multi-step agent workflows", templates, drag-and-drop nodes, typed inputs/outputs, export "the SDK code to run it yourself"; **OpenAI is deprecating Agent Builder; scheduled to shut down on November 30, 2026**; ChatKit remains. — [OpenAI Agent Builder docs](https://developers.openai.com/api/docs/guides/agent-builder)
- Practitioner limit: "once you need loops, dynamic fan-out, or complex error handling, you hit the limits of visual design pretty quickly". — [team400.ai](https://team400.ai/blog/2026-03-openai-agent-builder-visual-workflows)

**Google ADK 2.0**
- Graph workflows "allow you to define your agent logic as a graph of execution nodes and edges"; node types: agent nodes (Python passes an `Agent` directly; Go `workflow.NewAgentNode`), function nodes (Python plain functions, TS `node()`, Go `workflow.NewFunctionNode`), router/condition nodes (TS `createEvent({route})` with `DEFAULT_ROUTE`; Go `workflow.NewEmittingFunctionNode` with `StringRoute`/`IntRoute`/`BoolRoute`; array of routes = multi-branch dispatch), fan-in (`workflow.NewJoinNode`); edges in an `edges` list, Python tuples like `("START", a, b, c)` and a dict mapping route values to nodes; "Node return values pass to the next node" so you "do not need to write to session state". Three styles: graph-based, dynamic (code-based), prebuilt workflow agents (Sequential/Parallel/Loop). Python/TS/Go at v2.0.0. Known limitation: "not compatible with some third-party integrations". — [ADK graphs](https://adk.dev/graphs/)
- Dynamic workflows: orchestrator node calls children with `ctx.run_node()` / `ctx.runNode()` / `workflow.RunNode`; loops are ordinary `while/for`; parallel via `asyncio.gather` / `Promise.all` / `workflow.NewParallelWorker`; `@node(rerun_on_resume=True) async def my_workflow(ctx: Context, node_input: str) -> str: result = await ctx.run_node(my_node, node_input="hello")`. — [ADK dynamic workflows](https://adk.dev/graphs/dynamic/)
- ADK 2.0 "Workflow Runtime moves ADK from a hierarchical agent executor to a graph-based execution engine, where agents, tools, and functions are evaluated as individual nodes"; Go 2.0 release notes list "concurrent scheduler supporting static and dynamic graphs, conditional routing, fan-out/fan-in, parallel workers, per-node retries and timeouts, schema validation, and human-in-the-loop pause/resume". Python 2.0 GA 2026-05-19; Go 2.0 GA 2026-06-30. — [ADK 2.0](https://adk.dev/2.0/); [adk-go v2.0.0 release](https://newreleases.io/project/github/google/adk-go/release/v2.0.0)
- Agent Config (YAML, experimental; Python v1.11.0, Java v0.3.0, Go v0.3.0): `root_agent.yaml` with `name`, `model`, `description`, `instruction`, `agent_class: LlmAgent`, `tools:` (built-in `google_search` or Python function by module path e.g. `ma_llm.check_prime`), `sub_agents:` entries with `config_path`; run via `adk create --type=config my_agent`, `adk web` / `adk run` / `adk api_server`; code load `config_agent_utils.from_config("my_agent/root_agent.yaml")`; limitations: Gemini-only, `LangGraphAgent`/`A2aAgent` unsupported. — [ADK Agent Config](https://adk.dev/agents/config/)

**Microsoft Agent Framework**
- Workflows "define explicit, inspectable execution paths for coordinating code, agents, state, events, and human input. The framework provides functional and graph-based APIs over the same workflow run model." .NET: `WorkflowBuilder` "connects typed executors through edges and conditions, supports fan-out and fan-in execution, emits workflow and executor events, and checkpoints progress at superstep boundaries"; expose via `AsAIAgent()`. Python: functional `@workflow`/`@step` (experimental, native `if`/loops/`asyncio.gather`) vs graph `WorkflowBuilder` (edges, conditions, parallel edge groups, superstep execution); HITL `ctx.request_info()` vs `RequestInfoExecutor`; checkpointing "Per-`@step` result caching" vs "Superstep-boundary checkpoints"; `.as_agent()` on both. Go: `workflow.NewBuilder`, `inproc.Default`, `agentworkflow.New(...)`. Sub-workflows compose "workflows as executors in larger graphs". — [MAF workflow concepts](https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/)
- Graph build example (Python): `builder = WorkflowBuilder(start_executor=start_executor, checkpoint_storage=checkpoint_storage); builder.add_edge(start_executor, executor_b); ... workflow = builder.build()`. — [MAF checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints)
- Orchestrations provide "sequential, concurrent, handoff, group-chat, and Magentic patterns". — [MAF workflow capabilities](https://learn.microsoft.com/en-us/agent-framework/workflows/)
- Declarative YAML workflows (page dated 2026-10-01): .NET format `kind: Workflow / trigger: {kind: OnConversationStart, id: ..., actions: [ {kind: SetVariable, id, displayName, variable: Local.userName, value: =System.LastMessage.Text}, {kind: SendActivity, activity: =Local.message} ]}` (Power-Fx style expressions), loaded with `DeclarativeWorkflowBuilder.Build<string>(workflowPath, options)` and NuGet `Microsoft.Agents.AI.Workflows.Declarative.Foundry --prerelease`, requires a Microsoft Foundry project; Python format `name:/description:/inputs:/actions:` loaded with `from agent_framework.declarative import WorkflowFactory; workflow = factory.create_workflow_from_yaml_path("workflow.yaml")`, tools via `register_tool`, MCP via `DefaultMCPToolHandler`, HTTP via `DefaultHttpRequestHandler`; `DeclarativeWorkflowJsonOptions` is `[Experimental("MAAI001")]`. — [MAF declarative workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/declarative)
- MAF 1.0 GA: April 3, 2026 per [alicelabs](https://alicelabs.ai/en/insights/microsoft-agent-framework-guide-2026); April 2 per [rywalker AutoGen research](https://rywalker.com/research/autogen) — sources conflict by one day. AutoGen in maintenance mode, last feature release Sept 2025; Semantic Kernel in maintenance mode with support into 2027. — [rywalker](https://rywalker.com/research/autogen); [promptquorum SK review](https://www.promptquorum.com/power-local-llm/semantic-kernel-review)

**Pydantic AI / pydantic-graph**
- `pip install pydantic-graph`, standalone, no dependency on pydantic-ai; nodes are dataclasses `BaseNode[StateT, DepsT, RunEndT]`; "The `run` return annotation defines outgoing edges"; `End[RunEndT]` ends the run; `GraphRunContext` exposes `ctx.state`/`ctx.deps`; `GraphBuilder(input_type=int, output_type=int)` with `@g.step`, `g.node(...)`, `g.add(...)`, `g.edge_from(g.start_node).to(start)`, `g.build()`; `Graph.run()` / `Graph.iter()`; example: `async def run(self, ctx) -> Increment | End[int]: ...`; mermaid: `print(graph)` or `graph.render()` produces `stateDiagram-v2`, `direction` TB/LR/RL/BT. — [pydantic-graph docs](https://pydantic.dev/docs/ai/graph/graph/)

**Mastra**
- `createStep({ id, inputSchema, outputSchema, execute })` + `createWorkflow({ inputSchema, outputSchema }).then(step1).commit()`; `.parallel([step1, step2]).then(step3)` (outputs keyed by step id; any branch throwing fails the block); `.branch([[cond, stepA], [cond, stepB]])` (first match only); `.map(async ({ inputData }) => ...)`; loops `.dountil(step2, cond)`, `.dowhile(step2, cond)`, `.foreach(step1, { concurrency: 4 })` (default concurrency 1, order preserved); nested workflows as `.foreach(processDocWorkflow, {...})` or `.parallel([pipelineA, pipelineB])`; schema rule: each step's output should match the next's input, else reshape with `.map()`; control-flow methods accept labels `{id, description, metadata}`. — [Mastra control flow](https://mastra.ai/docs/workflows/control-flow)
- Mastra platform: Observability, Studio ("a hosted environment for testing agents and running workflows"), Server; blog (2025-10-22): "Mastra workflows now support suspend and resume in Playground". — [Mastra deployment](https://mastra.ai/docs/deployment/overview); [Mastra blog](https://mastra.ai/blog/resumeworkflows)

**Inngest AgentKit**
- "Networks can be thought of as while loops with memory (State) that call Agents and Tools until the Router determines that there is no more work to be done"; `createNetwork({ agents: [searchAgent, summaryAgent], defaultModel: openai({ model: 'gpt-4o' }), maxIter: 10, defaultState: new State({...}), router: ({ lastResult, callCount }) => { ... return undefined; } })`; router returns an Agent to run next or `undefined` to stop; three router styles: code callback ("supervised"), routing agent ("fully autonomous"), hybrid; `maxIter` recommended to avoid infinite loops. — [AgentKit networks](https://agentkit.inngest.com/concepts/networks); [AgentKit routers](https://agentkit.inngest.com/concepts/routers)

**Claude Agent SDK / Managed Agents**
- No graph; subagents declared via `agents={"code-reviewer": AgentDefinition(description=..., prompt=..., tools=["Read","Grep","Glob"], model="sonnet")}` in `ClaudeAgentOptions` (Python) / `options.agents` (TS); Claude "invokes subagents through the `Agent` tool"; `AgentDefinition` fields: `description`, `prompt`, `tools`, `disallowedTools`, `model`, `skills`, `memory`, `mcpServers`, `maxTurns`, `autoCompactWindow`, `background`, `omitClaudeMd`, `effort`, `permissionMode`; filesystem alternative: markdown files in `.claude/agents/`; for "dozens to hundreds of agents, use the `Workflow` tool, which moves the orchestration into a script the runtime executes outside the conversation context" (TS SDK v0.3.149+). — [Claude Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)
- Managed Agents: four concepts Agent / Environment / Session / Events; `multiagent` block `{"multiagent": {"type": "multiagent_20261001"}}` enables subagents, dynamic workflows (Claude "writes a workflow: a program that runs many agents in phases and combines their results"), and an advisor model; `predefined_agents` entries `{"type":"agent","id":...,"version":...}` or `{"type":"self"}`, up to 20 per list; delegation "one level deep", max 25 child threads. — [Managed Agents overview](https://platform.claude.com/docs/en/managed-agents/overview); [Managed Agents multiagent orchestration](https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration)

**smolagents**
- `managed_agents` is an optional parameter on `MultiStepAgent` (base of `CodeAgent` and `ToolCallingAgent`), with `name`/`description`; the manager calls a sub-agent like a tool; `CodeAgent` writes tool calls as Python code and is the default; older `ManagedAgent` wrapper (v1.5–1.7 docs) is superseded; docs label it "experimental API which is subject to change at any time". — [smolagents agents reference](https://huggingface.co/docs/smolagents/reference/agents.md)

**Visual alternatives**
- Flowise: AgentFlow V2 "multi-agent workflow engine with Agent, Tool, Condition, Loop, and Human-in-the-Loop nodes"; Apache 2.0; Node.js. Langflow: Python-based, "Export as Python code" listed as a feature. A 2026 comparison: "for production with audit requirements, durable execution, and mature observability, code frameworks remain superior, and the typical path is a PoC in a visual builder, then hardening in a code framework." — [madappgang comparison](https://madappgang.com/blog/open-source-visual-agent-builders-compared-flowise-vs-langflow-vs-n8n-vs-sim-studio-in-2026/); [leanware Langflow vs Flowise](https://www.leanware.co/insights/compare-langflow-vs-flowise)

### Inferences
- Only MAF (declarative YAML workflows with actions, Foundry-tied) and ADK (Agent Config YAML, agents+sub_agents only) have an official YAML for *topology*; CrewAI YAML covers agents/tasks (the "who/what"), while flow topology stays in Python decorators; LangGraph's `langgraph.json` is purely a deployment manifest. None of the code-first frameworks round-trips code <-> visual editor.
- The frameworks that give the most "product-designer-legible" graph are LangGraph, MAF graph API, Mastra and ADK graphs (explicit node/edge objects with typed schemas). CrewAI Flows and MAF functional API are easier to read as code but the topology is implicit (derived from decorator wiring).

### Gaps
- Could not retrieve verbatim YAML/code for ADK Agent Config `SequentialAgent`/`LoopAgent` in YAML, nor confirm whether ADK 2.0 graph workflows (edges/HITL nodes) can be declared in YAML (official HITL docs are Python/TS/Go only).
- No official source found on whether LangGraph Studio can *edit* a graph (all evidence says visualize/debug only).
- Did not verify Langflow's ability to export to OpenAI Agents SDK code.

---

## KQ2. Checkpointing / persistence, resume-from-failure, retries, timeouts, determinism controls

### Takeaway
Two persistence models dominate: **superstep/transition checkpointing inside the framework** (LangGraph checkpointers with `durability` modes, MAF superstep checkpoints + executor `on_checkpoint_save`, ADK per-node execution tracking with deterministic execution IDs, CrewAI `@persist`/SQLite, Mastra snapshots in a storage provider, pydantic-graph snapshot-per-node persistence) versus **delegating durability to a workflow engine** (OpenAI Agents SDK + Temporal, Pydantic AI + Temporal/DBOS/Prefect/Restate, Inngest AgentKit + Inngest steps, Mastra optional Inngest runner). The common determinism rule: code before a pause point re-executes on resume, so it must be idempotent (LangGraph interrupt, ADK `rerun_on_resume`, Temporal workflow determinism).

### Cited Findings

**LangGraph**
- Checkpointers "persist thread-scoped graph state for conversation continuity, human-in-the-loop workflows, time travel, and fault tolerance"; Stores persist cross-thread data; `InMemorySaver` (RAM, lost on restart), `SqliteSaver` (dev), `PostgresSaver`/`AsyncPostgresSaver` (production): `checkpointer = PostgresSaver.from_conn_string("postgresql://..."); checkpointer.setup()`; `graph = builder.compile(checkpointer=checkpointer, store=store)`; `thread_id` must be <255 chars for Postgres; "Checkpoints accumulate over long conversations. Prune them periodically"; "Subgraph state may not appear in the parent graph right away. Use a Store for data that must cross graph boundaries." — [LangGraph durable execution / persistence](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- Durability modes: `"exit"` persists only at end of run (fastest, no mid-run recovery); `"async"` writes in background while next step runs (a crash may lose some checkpoints); `"sync"` writes each checkpoint before the next step (most durable, slowest); passed as `graph.stream(..., durability="sync")` (JS `{ durability: "sync" }`); replaced `checkpoint_during` in v0.6.0. — [LangGraph durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution.md); [Pregel.astream reference](https://reference.langchain.com/python/langgraph/pregel/main/Pregel/astream)
- `langgraph-checkpoint` 4.1.0 (2026-05-12) "saves a snapshot of graph state at every superstep". — [langgraph-checkpoint on CERN PyPI mirror](https://simple-repository.app.cern.ch/project/langgraph-checkpoint)
- LangGraph 1.2.0 (2026-05-12, third-party report) extended checkpoint persistence to error handlers so a run can resume after a host crash mid-error-handling; "The guarantee requires Postgres, sync mode, and idempotent nodes"; resume is manual: "You still call invoke(None, config) with the correct thread_id. The framework does not poll, alert, or self-heal." — [groundy.com on LangGraph 1.2.0](https://groundy.com/articles/langgraph-1-2-0-makes-error-handler-resume-crash-durable-with-conditions/) (third-party; dates inconsistent across aggregators)
- Determinism: "When a node resumes, it restarts from the beginning, so any code before `interrupt()` runs again. Code before `interrupt()` must therefore be idempotent, and `interrupt()` calls must run in the same order on every execution"; don't wrap interrupts in bare try/except or `while True`. — [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- Loop guard: "The recursion limit sets the maximum number of super-steps the graph can execute during a single execution"; per-node `CachePolicy(ttl=3)`; `RetryPolicy` exists (linked, not detailed in fetched content). — [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)

**CrewAI**
- `@persist` class decorator ("SQLiteFlowPersistence by default"); fork from a prior run: `flow_2.kickoff(restore_from_state_id=flow_1.state.id)`; resume: `kickoff(inputs={"id": <uuid>})`. — [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)
- With external HITL providers "State is automatically saved when `HumanFeedbackPending` is raised and uses `SQLiteFlowPersistence` by default"; resume via `flow = ReviewFlow.from_pending(flow_id); result = flow.resume(feedback)` / `await flow.resume_async(feedback)`. — [CrewAI human feedback in flows](https://docs.crewai.com/en/learn/human-feedback-in-flows)
- Step counters, not cost caps: CrewAI `max_iter` and LangGraph `recursion_limit` "are structural step counters, not dollar-cap mechanisms" (see KQ7). — [arXiv 2606.04056](https://arxiv.org/pdf/2606.04056)

**OpenAI Agents SDK (+ Temporal)**
- Native durability = serializable `RunState`: `state = result.to_state(); state.to_string()` / `RunState.from_json(agent, stored)`; sticky `always_approve`/`always_reject` decisions survive serialization; options `context_serializer`, `context_deserializer`, `strict_context`, `include_tracing_api_key`; "serialized state includes your app context, so avoid storing secrets"; `RunState.from_json()` "do[es] not authenticate the snapshot", so keep snapshots server-side and prevent replayed resumes. — [OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- Temporal integration (`temporalio-openai-agents`, "Release stage: Generally Available", requires Temporal 1.34+): write `@workflow.defn class HelloWorldAgent: @workflow.run async def run(self, prompt): result = await Runner.run(agent, input=prompt)`; model invocations "are automatically routed through Temporal activities"; `OpenAIAgentsPlugin(model_params=ModelActivityParameters(start_to_close_timeout=timedelta(seconds=30)))` on the client; tools: `activity_as_tool()` runs as a Temporal activity (I/O), `@function_tool` runs in the workflow (deterministic logic only), hosted tools at the provider; MCP operations each become activities; `WorkflowSession` for multi-turn; paused approvals serialized via `RunState.to_string()` and resumable "including across `continue_as_new`"; streaming experimental, OTel public preview. The integration moved from `temporalio.contrib.openai_agents` to the standalone package `temporalio.openai_agents`. — [temporalio/ai-integrations openai_agents](https://github.com/temporalio/ai-integrations/tree/main/python/openai_agents); [Temporal docs migration note](https://docs.temporal.io/ai/openai-agents)

**Google ADK 2.0**
- "ADK tracks each node execution, and successful sub-nodes are skipped when a workflow resumes"; orchestrators need `rerunOnResume: true`; leaf nodes default to `false` ("routes the resume payload to the next node"); "After a resume, only failed or interrupted parallel workers re-execute"; execution IDs are "deterministic IDs from the parent ID and a counter, which drive checkpointing and re-run order"; custom run IDs discouraged and must be deterministic. — [ADK dynamic workflows](https://adk.dev/graphs/dynamic/)
- Go 2.0 release notes: "per-node retries and timeouts, schema validation, and human-in-the-loop pause/resume"; HITL can "halt execution, wait for human feedback or approval, and resume from a checkpoint". — [adk-go v2.0.0](https://newreleases.io/project/github/google/adk-go/release/v2.0.0)
- Upgrade caveat: "storage is incompatible across versions, and using ADK 2.0 code with ADK 1.x databases risks data loss" (third-party). — [opensourceforu](https://opensourceforu.com/2026/09/google-unveils-agent-development-kit)

**Microsoft Agent Framework**
- "Checkpoints are created at the end of each superstep"; captured: executor states, pending messages for next superstep, pending requests/responses, shared states. Python 1.13.0 adds an entry checkpoint before the first superstep and when responses are delivered, making "the complete workflow run replayable" (minor breaking changes to iteration counts/ordering). Storage providers: `InMemoryCheckpointStorage`, `FileCheckpointStorage("/var/lib/agent-framework/checkpoints")`, `CosmosCheckpointStorage` (package `agent-framework-azure-cosmos`, partition key `/workflow_name`); resume: `async for event in workflow.run(checkpoint_id=saved_checkpoint.checkpoint_id, stream=True)` or rehydrate a new instance with `checkpoint_storage=`; .NET `CheckpointManager.CreateInMemory()`, `run.RestoreCheckpointAsync(savedCheckpoint)`, `InProcessExecution.ResumeStreamingAsync(newWorkflow, savedCheckpoint, checkpointManager)`; custom executors implement `on_checkpoint_save()` / `on_checkpoint_restore(state)` (Python) or `OnCheckpointingAsync` / `OnCheckpointRestoredAsync` (.NET); "A rehydrated workflow must preserve the topology and executor identities"; .NET agents need stable `ChatClientAgentOptions.Id`; security: Python file/Cosmos stores use pickle with a restricted unpickler and `allowed_checkpoint_types`; "Checkpoint storage is a trust boundary." — [MAF checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints)
- Third-party write-up: each checkpoint carries an ID, iteration counter, in-flight messages, per-executor state "and a hash of the graph signature, so that resumption can detect graph drift". — [ravichaganti.com](https://ravichaganti.com/blog/visualizing-and-checkpointing-workflows-in-microsoft-agent-framework/)

**Pydantic AI / pydantic-graph**
- pydantic-graph "provides state persistence, with the state recorded after each node is run"; `Graph.run` defaults to `SimpleStatePersistence`; `FileStatePersistence` (import `pydantic_graph.persistence.file`), `FullStatePersistence`; `Graph.iter_from_persistence(persistence)` loads "the next snapshot still marked as created and changes its status to pending". — [pydantic_graph persistence API](https://pydantic.dev/docs/ai/api/pydantic_graph/persistence); [pydantic-ai issue #1684](https://github.com/pydantic/pydantic-ai/issues/1684)
- Durable execution for agents: co-maintained engines Temporal, DBOS, Prefect, Restate, AWS Lambda; external: Kitaru, Apache Airflow, Absurd, pgtask; "Each engine wraps every model request and tool call as its own durable unit"; one durable engine per agent (second raises `UserError`); "Durability is not storage" — engines don't store chat threads. — [Pydantic AI durable execution](https://pydantic.dev/docs/ai/durable_execution/overview/)

**Mastra**
- "Suspension saves the current execution state as a snapshot"; snapshots "are stored in your configured storage provider and persist across deployments and application restarts"; `suspend()` sets status `suspended`; `.sleep(ms)` / `.sleepUntil(date)` set status `waiting`; recover a run with `workflow.getWorkflowRunById(runId)` and `createWorkflowStateReader(state)` (`getSuspendedStep()`, `getResumeLabel()`, `foreachIndex`); `result.suspended` lists nested paths like `['nested-workflow', 'step-1']`. — [Mastra suspend and resume](https://mastra.ai/docs/workflows/suspend-and-resume)
- "Workflows use the built-in execution engine by default. Inngest is an option that adds step memoization and automatic retries"; workers can run orchestration, cron, background tool execution separately from the API server. — [Mastra deployment](https://mastra.ai/docs/deployment/overview)

**Inngest AgentKit**
- With the Inngest SDK the network gets "Retrieable and parallel tool calls"; LLM requests are offloaded (serverless-friendly); "Retries for agents and tools are configured separately"; deploy via `createServer` from `@inngest/agent-kit/server`, sync with the Inngest dashboard at `/api/inngest`. — [AgentKit deployment](https://agentkit.inngest.com/concepts/deployment)
- Third-party: "network agent and tool calls can be retried automatically, and the underlying step machinery can cache results for durability". — [PromptLayer glossary](https://www.promptlayer.com/glossary/inngest-agent-kit)

**Claude Managed Agents / Agent SDK**
- "Claude Managed Agents is stateful by design: sessions are long-running, resume cleanly after pauses, and store conversation history, sandbox state, and outputs server-side"; "Event history is persisted server-side"; therefore not eligible for ZDR/HIPAA BAA. — [Managed Agents overview](https://platform.claude.com/docs/en/managed-agents/overview)
- Agent SDK: subagent `maxTurns` returns output "marked as partial" and you can resume; resume a subagent by `resume: sessionId` plus the `agentId` parsed from the Agent tool result; "Subagent transcripts are stored in separate files and persist independently of the main conversation." — [Claude Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)
- Budgets: Managed Agents session `budget: {"type":"limit","max_list_cost":{"amount":"125","currency":"USD"}}` — "When the session reaches the cap, it pauses and goes idle with the stop reason `budget_reached`"; enforced between model requests. Agent SDK: `max_budget_usd` / `maxBudgetUsd`, `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` (default 3), `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (default 20); hitting budget ends the query with `error_max_budget_usd`. — [Managed Agents sessions](https://platform.claude.com/docs/en/managed-agents/sessions); [Claude Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)

### Inferences
- Native framework checkpointing (LangGraph/MAF/ADK/Mastra) is "checkpoint the orchestrator's state," not "make arbitrary side effects idempotent"; all of them push idempotency onto node authors. Temporal-style integrations (OpenAI SDK, Pydantic AI) instead make *every model call and tool call* a retried activity, which is closer to what long-running engineering tasks need, at the cost of running a workflow engine.
- Only Anthropic's stack and the Agent SDK expose first-class **dollar budgets** as a runtime control; others expose iteration counters.

### Gaps
- Official LangGraph `RetryPolicy` signature and defaults were linked but not captured in fetched content.
- Did not find official docs on ADK Python per-node retry/timeout API (only Go release notes mention it).
- No official Inngest statement on exactly which AgentKit calls become memoized steps.
- Official LangGraph 1.2.x release notes were not retrieved; version/date claims rely on third-party trackers that disagree.

---

## KQ3. Human-in-the-loop primitives — how does each framework express "wait for human approval, then continue"? (with code)

### Takeaway
Four HITL shapes exist: (1) **interrupt inside a node, resume by re-invoking with a value** (LangGraph `interrupt()`/`Command(resume=)`, Mastra `suspend()`/`run.resume()`, CrewAI `@human_feedback` + `HumanFeedbackPending`/`from_pending().resume()`, ADK `RequestInput` events); (2) **typed request/response ports** (MAF `ctx.request_info()` + `@response_handler`, .NET `RequestPort`, re-emitted after checkpoint restore); (3) **tool-call approval gates** where the run ends with pending approvals and resumes from serialized state (OpenAI Agents SDK `needs_approval` + `RunState.approve/reject`, Pydantic AI `requires_approval` + `DeferredToolRequests/Results`, ADK tool `requireConfirmation`, MAF `function_approval_request`, Managed Agents `user.tool_confirmation`); (4) **durable wait-for-event** (Inngest `step.waitForEvent` inside a tool, Temporal signals). Anthropic's stack adds "steer or interrupt" mid-run via events rather than a graph pause.

### Cited Findings

**LangGraph**
```python
from langgraph.types import interrupt, Command

def approval_node(state: State):
    approved = interrupt("Do you approve this action?")   # pauses; payload surfaced to caller
    return {"approved": approved}

config = {"configurable": {"thread_id": "thread-1"}}
graph.stream_events({"input": "data"}, config=config, version="v3")
graph.stream_events(Command(resume=True), config=config, version="v3")   # resume
```
- Requires a checkpointer + `thread_id`; `invoke` surfaces interrupts under `result["__interrupt__"]`, docs recommend `stream_events(..., version="v3")` and `stream.interrupts`; patterns: approve/reject (`Command(resume=True/False)`, node returns `Command(goto="proceed" if decision else "cancel")`), review-and-edit (`Command(resume="The edited and improved text")`), tool approval (call `interrupt()` inside the tool; resume value can override tool args); `Command(resume=...)` is the only Command form accepted as input; interrupt response schemas need `langgraph>=1.2.12`; as of v1.0 `interrupt` is the recommended way and `NodeInterrupt` is deprecated (removed in v2.0). — [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts); [LangGraph add HITL](https://docs.langchain.com/oss/python/langgraph/add-human-in-the-loop)

**CrewAI Flows (>=1.8.0)**
```python
from crewai.flow.flow import Flow, start, listen, or_
from crewai.flow.human_feedback import human_feedback

class ReviewFlow(Flow):
    @start()
    def generate_draft(self): ...
    @listen(or_("generate_draft", "needs_revision"))
    @human_feedback(message="Do you approve this content for publication?",
                    emit=["approved", "rejected", "needs_revision"],
                    llm="gpt-4o-mini", default_outcome="needs_revision")
    def review(self, draft): return draft
    @listen("approved")
    def publish(self, result): ...
```
- "The human's free-form feedback is interpreted by an LLM and collapsed into one of the specified outcomes"; params `message`, `emit`, `llm` (required with `emit`), `default_outcome`, `metadata`, `provider`, `learn`, `learn_source`, `learn_strict` (no `timeout`); default `ConsoleProvider` blocks on console input; async/external: custom `HumanFeedbackProvider.request_feedback(context: PendingFeedbackContext, flow)` raises `HumanFeedbackPending`, `kickoff()` returns the pending object, resume with `ReviewFlow.from_pending(flow_id).resume(feedback)`; `flow.last_human_feedback` and `flow.human_feedback_history`. — [CrewAI human feedback in flows](https://docs.crewai.com/en/learn/human-feedback-in-flows)

**OpenAI Agents SDK**
```python
@tool(needs_approval=True)
async def cancel_order(order_id: int) -> str: ...

async def requires_review(_ctx, params, _call_id) -> bool:
    return "refund" in params.get("subject", "").lower()
@tool(needs_approval=requires_review)
async def send_email(subject: str, body: str) -> str: ...

result = await Runner.run(agent, "...")
while result.interruptions:
    state = result.to_state()
    STATE_PATH.write_text(state.to_string())            # persist, possibly other process
    state = await RunState.from_json(agent, json.loads(STATE_PATH.read_text()))
    for interruption in result.interruptions:          # ToolApprovalItem: tool_name, arguments
        state.approve(interruption, always_approve=False)  # or state.reject(interruption)
    result = await Runner.run(agent, state)
```
- Callable approval rules "fail closed when the SDK cannot safely inspect the arguments"; approvals also apply after handoffs and inside nested `Agent.as_tool()` runs (surface on the outer run); reuse the same session when resuming. — [OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- Under Temporal the same `needs_approval` pause works with state serialized via `RunState.to_string()` and resumed "including across `continue_as_new`". — [temporalio/ai-integrations](https://github.com/temporalio/ai-integrations/tree/main/python/openai_agents)

**Google ADK 2.0**
- Graph HITL nodes yield `RequestInput(message=..., payload=..., response_schema=...)` (Python import `google.adk.events`; TS `@google/adk`; Go `session.RequestInput`); "Execution pauses until the user replies, and the reply is passed to the next node"; TS default `rerunOnResume: false` (interrupted node completes with the reply as output), nodes that call `ctx.runNode()` need `rerunOnResume: true`; Go uses `workflow.ResumeOrRequestInput` returning `ErrNodeInterrupted` and `InterruptID`; `response_schema` "is sent on the interrupt so a client can build a form" but is not validated in Go; these nodes "don't call an AI model, so the pause is predictable". Separate mechanism for LLM agents: tool confirmation (`requireConfirmation: true` on TS `FunctionTool`, `RequireConfirmation` in Go, `ctx.RequestConfirmation`). Supported in Python/TS/Go v2.0.0. — [ADK human input](https://adk.dev/graphs/human-input/)
- In dynamic workflows TS `runNode()` doesn't throw on child interrupt — "Check `interruptIds` before using `output`, or the workflow may proceed with no answer." — [ADK dynamic workflows](https://adk.dev/graphs/dynamic/)

**Microsoft Agent Framework**
```python
class JudgeExecutor(Executor):
    @handler
    async def handle_guess(self, guess: int, ctx: WorkflowContext[int, str]) -> None:
        if guess == self._target_number:
            await ctx.yield_output("found")
        else:
            await ctx.request_info(request_data=NumberSignal(hint="below"), response_type=int)
    @response_handler
    async def on_human_response(self, original_request: NumberSignal, response: int, ctx) -> None:
        await self.handle_guess(response, ctx)

stream = workflow.run(25, stream=True)                       # events with type == "request_info"
stream = workflow.run(stream=True, responses={request_id: 42})  # resume with answers
```
- .NET: `RequestPort.Create<NumberSignal, int>("GuessNumber")` added as a node; `RequestInfoEvent` -> `handle.SendResponseAsync(requestInputEvt.Request.CreateResponse(guess))`; Go: `workflow.RequestPort{ID, Request, Response}` + `run.Resume(ctx, response)`. For agent orchestrations tool approval emits `RequestInfoEvent` with `ToolApprovalRequestContent` (C#/Go) or `Content` with `type == "function_approval_request"` (Python); handoff orchestration "is interactive by default"; "When a checkpoint is created, pending requests are also saved... any pending requests will be re-emitted as `RequestInfoEvent` objects", and you can pass both `checkpoint_id` and `responses` to `workflow.run(...)`. — [MAF HITL](https://learn.microsoft.com/en-us/agent-framework/workflows/human-in-the-loop)

**Pydantic AI**
```python
@agent.tool_plain(requires_approval=True)
def delete_file(path: str) -> str: ...

@agent.tool
def update_file(ctx: RunContext, path: str, content: str) -> str:
    if path in PROTECTED_FILES and not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={'reason': 'protected'})
    ...

agent = Agent('openai:gpt-5.2', output_type=[str, DeferredToolRequests])
result = agent.run_sync('Delete `__init__.py` ...')
requests = result.output                     # DeferredToolRequests: .approvals (ToolCallParts)
results = DeferredToolResults()
for call in requests.approvals:
    results.approvals[call.tool_call_id] = True   # or ToolDenied('reason')
result = agent.run_sync('Now create a backup', message_history=result.all_messages(),
                        deferred_tool_results=results)
```
- Resumption is "a new run. It gets its own `run_id`"; `requests.build_results(approve_all=True)`; `ConversationTypeAdapter` stores paused `result.conversation`; `HandleDeferredToolCalls` capability resolves approvals in-process; "Approval is not an authorization check against untrusted clients." — [Pydantic AI deferred tools](https://pydantic.dev/docs/ai/deferred-tools/)
- pydantic-graph: closest documented interactive node is the vending-machine example using `Prompt.ask('Insert coins')`; pause-and-resume across processes is done by ending the run and later calling `iter_from_persistence` on the same persistence file. — [pydantic-ai graph.md (mirror)](https://glama.ai/mcp/servers/@pydantic/pydantic-ai/blob/b58e6e4cb64726c5be9938d4660038ec4a5900e2/docs/graph.md); [pydantic-ai issue #1684](https://github.com/pydantic/pydantic-ai/issues/1684)

**Mastra**
```ts
const approvalStep = createStep({
  id: 'step-1', inputSchema, outputSchema,
  resumeSchema: z.object({ approved: z.boolean() }),
  suspendSchema: z.object({ reason: z.string() }),
  execute: async ({ inputData, resumeData, suspend, bail }) => {
    if (!resumeData?.approved) return await suspend({ reason: 'Human approval required.' });
    ...
  },
});
const result = await run.start({ inputData: { userEmail: 'alex@example.com' } });
if (result.status === 'suspended') { /* later, any process: */ }
await run.resume({ step: 'step-1', resumeData: { approved: true } });
```
- `resume()` "can be called from any part of the app, including HTTP handlers, event handlers, or timers"; resume by `runId` after `workflow.createRun({ runId })`; `bail()` "stop[s] workflow execution at a step without triggering an error" (success status) for explicit rejection; `closeOnSuspend` / `resumeStream` for streaming. — [Mastra human-in-the-loop](https://mastra.ai/docs/workflows/human-in-the-loop); [Mastra suspend and resume](https://mastra.ai/docs/workflows/suspend-and-resume)

**Inngest AgentKit**
- Pattern: a `createTool` named `ask_developer` whose handler calls `step.waitForEvent("developer.response", { event: "app/support.ticket.developer-response", timeout: "4h", match: "data.ticketId" })`, pausing the network until the event arrives; the network is wrapped in `inngest.createFunction(...)` triggered by `app/support.ticket.created`; tool "returns an error if `step` is missing, so it only works inside an Inngest function". — [AgentKit HITL](https://agentkit.inngest.com/advanced-patterns/human-in-the-loop)

**Claude Managed Agents / Agent SDK**
- Managed Agents: client steers or interrupts via events ("Send additional user events to guide the agent mid-execution, or interrupt it to change direction"); response events include `user.tool_confirmation`, `user.tool_result`, `user.custom_tool_result`, `user.interrupt`; the sessions page links "stream responses and handle tool confirmations". — [Managed Agents overview](https://platform.claude.com/docs/en/managed-agents/overview); [Managed Agents sessions](https://platform.claude.com/docs/en/managed-agents/sessions)
- Agent SDK: subagents carry `permissionMode`; tools omitted from `tools` are simply absent ("no permission prompt or error"); approval is modelled as permissions (`allowed_tools` auto-approve) rather than a graph pause. — [Claude Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)

**Temporal (generic)**
- Temporal's AI cookbook includes a recipe for human-in-the-loop approval "using Temporal Signals in Python"; tutorial series "Building Deep Research Agents with the OpenAI Agents SDK" (updated 2026-09-02) builds "a workflow that manages state and human-in-the-loop interactions" and tests "durability by surviving crashes". — [Temporal AI docs](https://docs.temporal.io/ai); [Temporal deep research tutorial](https://learn.temporal.io/tutorials/ai/deep-research/)

### Inferences
- For a product designer, the key UX difference: LangGraph/Mastra/MAF/ADK pause *the graph* at a known node and expose a typed payload (ADK `response_schema`, Mastra `suspendSchema`/`resumeSchema`, MAF `request_data`/`response_type`) that a client can render as a form; OpenAI/Pydantic AI pause *a tool call* and expose tool name + arguments. CrewAI is unique in letting an LLM interpret free-text feedback into a routing outcome.
- Re-execution semantics differ and matter for side effects: LangGraph re-runs the node from the top; ADK leaf nodes complete with the reply (no re-run) unless `rerun_on_resume`; MAF resumes by delivering the response to a handler (no re-run of the requesting handler); OpenAI/Pydantic AI start a new run with history.

### Gaps
- Could not fetch the Managed Agents `events-and-streaming` page for the exact `user.tool_confirmation` payload shape.
- No official Inngest documentation on what happens to AgentKit network state if the 4h `waitForEvent` times out beyond the tool returning an error.
- CrewAI `@human_feedback` in *Crews* (task-level `human_input=True`) was not re-verified in this pass; only Flows were covered.

---

## KQ4. What is passed between nodes: state schema, typed outputs, files/artifacts?

### Takeaway
Most graph frameworks pass **typed in-memory state or messages** (LangGraph TypedDict/Pydantic with reducers; CrewAI `Flow[PydanticModel]` + `result.raw`; MAF typed messages routed by type annotations on `@handler`; ADK node return values; pydantic-graph dataclass nodes with `ctx.state`; Mastra zod `inputSchema/outputSchema` per step; AgentKit `State<T>.data` + `state.results` history). **Files/artifacts** are first-class only in sandbox-centric runtimes: Claude Managed Agents sessions have a persistent filesystem shared by all agents in a session; LangChain Deep Agents add a virtual filesystem; LangGraph's `Store` is the cross-thread/cross-graph escape hatch.

### Cited Findings
- LangGraph: state keys with reducers (`add_messages`); `Command(update=...)`; `Send` for parallel fan-out payloads; Stores for "application-defined data across threads"; "Subgraph state may not appear in the parent graph right away. Use a Store for data that must cross graph boundaries." — [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api); [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- CrewAI: `Flow[ExampleState]` with `class ExampleState(BaseModel)`; crew outputs flow back as `result.raw`; `@human_feedback` method output is shown to the reviewer alongside `message`. — [CrewAI Flows](https://docs.crewai.com/en/concepts/flows); [CrewAI HITL](https://docs.crewai.com/en/learn/human-feedback-in-flows)
- MAF: graph API is for "type-validated message routing"; `@handler async def handle_guess(self, guess: int, ctx: WorkflowContext[int, str])`; `ctx.send_message(...)`, `ctx.yield_output(...)`; `@response_handler` matched "based on the type annotations of the `original_request` and `response` parameters"; built-in `AgentExecutorCheckpointState` holds `cache`, `full_conversation`, `agent_session`, `pending_agent_requests`, `pending_responses_to_agent`; shared states are checkpointed. — [MAF concepts](https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/); [MAF HITL](https://learn.microsoft.com/en-us/agent-framework/workflows/human-in-the-loop); [MAF checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints)
- ADK 2.0: "Node return values pass to the next node, so... you do not need to write to session state"; `ctx.run_node()` "Results come back directly"; `RequestInput(payload=..., response_schema=...)` carries structured data to the client. — [ADK graphs](https://adk.dev/graphs/); [ADK human input](https://adk.dev/graphs/human-input/)
- pydantic-graph: `state_type` on `GraphBuilder`, nodes mutate `ctx.state`; `deps_type` for dependency injection; node fields are the edge payload (`Increment(self.foo)`); `End[int]` typed output. — [pydantic-graph](https://pydantic.dev/docs/ai/graph/graph/)
- Pydantic AI agents: `output_type=[str, DeferredToolRequests]` — run output is a typed union; `DeferredToolRequests.metadata` keyed by tool call ID. — [Pydantic AI deferred tools](https://pydantic.dev/docs/ai/deferred-tools/)
- Mastra: zod `inputSchema`/`outputSchema`/`resumeSchema`/`suspendSchema`; `.parallel` outputs keyed by step id; `.map()` with `getStepResult()`, `getInitData()`, `mapVariable()`; "With `z.object()`, undeclared keys are stripped" in loops. — [Mastra control flow](https://mastra.ai/docs/workflows/control-flow); [Mastra HITL](https://mastra.ai/docs/workflows/human-in-the-loop)
- AgentKit: `createState<NetworkState>({ username: 'bar' })`, `state.data` ("A standard, mutable object which can be updated and modified within tools"), `state.results` = `InferenceResult[]` with `agent`, `input`, `prompt`, `history`, `output`, `toolCalls`, `raw`; state passed via `network.run(query, { state })`. — [AgentKit state reference](https://agentkit.inngest.com/reference/state)
- OpenAI Agents SDK: `RunState` serializes the whole run including app context; `ToolApprovalItem` exposes `tool_name`/`arguments`. — [OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- Claude Managed Agents: "each session has its own filesystem, context window, and event stream" (third-party summary); official: "All agents share the same sandbox, filesystem, and vault credentials, but each agent runs in its own session thread, a context-isolated event stream"; with dynamic workflows "Context and results are passed programmatically from one agent to another"; sessions keep "Persistent filesystems and conversation history across multiple interactions"; `initial_events` accept up to 100 file-sourced `document` blocks and 32 MB bodies; `user.define_outcome` with a `rubric`. — [Managed Agents multiagent](https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration); [Managed Agents overview](https://platform.claude.com/docs/en/managed-agents/overview); [Managed Agents sessions](https://platform.claude.com/docs/en/managed-agents/sessions); [hatchworks](https://hatchworks.com/blog/claude/claude-agent-sdk-and-managed-agents/)
- Claude Agent SDK: "The only content you pass from parent to subagent is the Agent tool's prompt string, so include any file paths, error messages, or decisions the subagent needs directly in that prompt"; "only its final message returns to the parent"; since v2.1.210 the final message is scanned for instruction-shaped patterns. — [Claude Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)
- LangChain Deep Agents: "planning, subagents, a virtual filesystem, and long-term memory built in"; runs on the LangGraph runtime. — [LangChain build overview](https://docs.langchain.com/oss/python/build-overview); [LangChain OSS overview](https://www.langchain.com/oss-overview)

### Inferences
- No graph framework has a native "artifact" type (file handle with lineage) between nodes; files are either serialized into state (bad for large outputs), referenced by path in a shared filesystem (Managed Agents, Deep Agents, Agent SDK), or kept in an external store (LangGraph Store, MAF shared state). A product needing PR diffs / build logs as node outputs will have to define that artifact abstraction itself.

### Gaps
- No official source on size limits for LangGraph checkpoint payloads or Mastra snapshots.
- Did not verify Mastra's "agents as steps" (`createStep(agent)`) or tools-as-steps in the fetched page.

---

## KQ5. Deployment/runtime story and observability/tracing

### Takeaway
Every vendor now has a hosted runtime: LangSmith Deployment (Agent Server, Studio; ex-LangGraph Platform), CrewAI AMP (REST deploy, Crew Studio, traces, webhook streaming), Mastra platform (Observability, Studio, Server) plus Vercel/Netlify/Cloudflare deployers, Inngest (durable functions), Microsoft Foundry (declarative workflows, Cosmos checkpoints), Google Agent Engine via ADK CLI, Anthropic Managed Agents (sandboxed sessions, $0.08/agent-hour reported by third parties), Temporal Cloud for OpenAI/Pydantic AI. Observability converges on OpenTelemetry: Langfuse "is based on OpenTelemetry"; CrewAI->Langfuse via OpenLit OTel; MAF exports spans/metrics; Temporal OTel in public preview.

### Cited Findings
- LangSmith Deployment: `langgraph.json` graphs become assistants; "Running several agents on one deployment reduces cost"; Studio works with "graphs deployed on LangSmith or running locally through the Agent Server"; one-click deployment to LangSmith Cloud; third-party notes LangGraph Platform was renamed LangSmith Deployment and reached GA. — [LangSmith application structure](https://docs.langchain.com/langsmith/application-structure); [LangSmith Studio](https://docs.langchain.com/langsmith/studio); [rubinlake radar](https://rubinlake.com/en/technology-radar/ai-and-data-engineering/langgraph)
- CrewAI AMP: "Access your deployed crews via REST API", Crew Studio no-code, "Monitor your crews with detailed execution traces and logs", webhook streaming "Stream real-time events and updates to your systems", GitHub integration and CLI deploy. — [CrewAI AMP](https://docs-platform.crewai.com/platform/en/introduction)
- CrewAI tracing to Langfuse "using OpenTelemetry via the OpenLit SDK", Langfuse OTel endpoint `/api/public/otel`. — [CrewAI Langfuse docs](https://docs.crewai.com/en/observability/langfuse)
- Langfuse: "Langfuse is based on OpenTelemetry"; integrates "via native integrations, OpenTelemetry, and APIs/SDKs". — [Langfuse integrations](https://langfuse.com/integrations)
- MAF capabilities list: Observability ("Export workflow spans, metrics, events, and delivery status"), Visualization ("Render and export workflow topology"); declarative workflows target Microsoft Foundry; `CosmosCheckpointStorage` for "production, distributed, cross-process workflows"; `FoundryCheckpointStore` with `ResponsesHostServer`. — [MAF capabilities](https://learn.microsoft.com/en-us/agent-framework/workflows/); [MAF checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints)
- Temporal + OpenAI Agents: GA, OTel "public preview", sandbox agents "pre-release". — [temporalio/ai-integrations](https://github.com/temporalio/ai-integrations/tree/main/python/openai_agents)
- Mastra: Node 22.13+/Bun/Deno/Cloudflare; "Mastra includes optional built-in deployers for Vercel, Netlify, and Cloudflare"; Hono-based server via `mastra build`; Mastra platform = Observability (hosted traces/logs/metrics), Studio, Server; Sandbox deploys to Vercel Sandbox / E2B. — [Mastra deployment](https://mastra.ai/docs/deployment/overview)
- AgentKit: "Step-by-step traces show agent inputs/outputs and token usage"; deploy anywhere, sync app URL in Inngest dashboard. — [AgentKit deployment](https://agentkit.inngest.com/concepts/deployment)
- ADK: run via `adk web`/`adk run`/`adk api_server`; LangSmith can host ADK apps through `deployments-wrap-sdk`. — [ADK Agent Config](https://adk.dev/agents/config/); [LangSmith application structure](https://docs.langchain.com/langsmith/application-structure)
- Anthropic Managed Agents: hosted harness with "built-in prompt caching, compaction"; cloud sandbox or self-hosted sandbox; SSE streaming; scheduled deployments (cron); vaults for MCP OAuth; session `budget`; available on Claude Platform on AWS with differences; third-party reports launch 2026-04-08 and "$0.08 per agent runtime hour, plus standard Claude API token costs". — [Managed Agents overview](https://platform.claude.com/docs/en/managed-agents/overview); [Managed Agents sessions](https://platform.claude.com/docs/en/managed-agents/sessions); [hatchworks](https://hatchworks.com/blog/claude/claude-agent-sdk-and-managed-agents/); [noqta](https://www.noqta.tn/en/news/anthropic-claude-managed-agents-enterprise-ai-platform-2026)
- Claude Agent SDK: Python + TypeScript; renamed from Claude Code SDK on 2025-09-29 (third-party); `total_cost_usd` on result messages. — [hatchworks](https://hatchworks.com/blog/claude/claude-agent-sdk-and-managed-agents/); [Claude Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)

### Inferences
- The runtime stories split into "graph server" (LangSmith Deployment, Mastra Server, CrewAI AMP) vs "durable function engine" (Inngest, Temporal) vs "sandboxed agent session service" (Managed Agents). Only the last gives a persistent filesystem and a cost budget as runtime primitives.

### Gaps
- Langfuse's per-framework integration list (Mastra, ADK, OpenAI Agents SDK, Claude Agent SDK) could not be extracted from the fetched page.
- Official Managed Agents pricing page was not fetched; the $0.08/hour figure is third-party.
- LangSmith Deployment cron/webhook/double-texting details were not in the fetched page.

---

## KQ6. Which frameworks treat an entire autonomous agent session (not a single LLM call) as a node, and how?

### Takeaway
"Agent session as a node" is native in: Google ADK 2.0 (an `LlmAgent` is a graph node and dynamic workflows call it via `ctx.run_node`), Microsoft Agent Framework (`AgentExecutor`, "Agents in workflows", and `Workflow.as_agent()` for the inverse), LangGraph (a compiled subgraph or Deep Agent is a node; LangChain's own framing: "a node can now be a full agent run"), CrewAI Flows (a whole Crew `kickoff()` inside a `@listen` step), Mastra (nested workflows / agents as steps), AgentKit (every network iteration is an agent run). Anthropic's Managed Agents and the Claude Agent SDK invert the model: the *session* is the unit and sub-sessions (threads/subagents, dynamic workflow runs) are spawned by the agent, with `maxTurns`, budgets and resume by `agentId`.

### Cited Findings
- ADK: "LLM agents can be graph nodes. In Python you pass an `Agent` directly. In Go, `workflow.NewAgentNode` is the equivalent"; TS `ctx.runNode()` "accepts an `LlmAgent` directly"; ADK 2.0 runtime evaluates "agents, tools, and functions... as individual nodes". — [ADK graphs](https://adk.dev/graphs/); [ADK dynamic](https://adk.dev/graphs/dynamic/); [ADK 2.0](https://adk.dev/2.0/)
- MAF: "Agent Executor integrates agents into workflow graphs"; "Agents in workflows: Use agents as workflow participants and executors"; "Workflows as agents: Expose a workflow through the standard agent interface"; when exposed as an agent, "external input requests from RequestInfoExecutor are surfaced as function calls"; `AgentExecutorCheckpointState` persists the agent's `agent_session` and `full_conversation`. — [MAF concepts](https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/); [MAF capabilities](https://learn.microsoft.com/en-us/agent-framework/workflows/); [MAF as agents](https://learn.microsoft.com/en-us/agent-framework/user-guide/workflows/as-agents); [MAF checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints)
- LangGraph: subgraphs via `Command(graph=Command.PARENT)`; `@entrypoint` compiles to a single node; Deep Agents (planning, subagents via a task tool, virtual filesystem) "runs on the LangGraph runtime"; LangChain ships "dcode", a terminal coding agent on the Deep Agents SDK. A LangChain-adjacent podcast transcript: "a node can now be a full agent run", with a docs-agent example of "fixed operations, classifier and synthesis steps, then agents doing open-ended work in codebases". — [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api); [LangChain build overview](https://docs.langchain.com/oss/python/build-overview); [podcast transcript "3 years of graph engineering with LangGraph"](https://sthbxxuzklalnaiaeagd.supabase.co/storage/v1/object/public/exploring-next-audio/episodes/3-years-of-graph-engineering-with-langgraph-1784737435422.txt)
- CrewAI: `PoemCrew().crew().kickoff(inputs=...)` inside a `@listen` flow method — a whole multi-agent crew run is one flow step. — [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)
- Mastra: nested workflows as `.foreach` body / `.parallel` branch. — [Mastra control flow](https://mastra.ai/docs/workflows/control-flow)
- AgentKit: "Each pass runs the agent, then saves its result to state, then hands control back to the router"; `maxIter` bounds the loop. — [AgentKit networks](https://agentkit.inngest.com/concepts/networks)
- Claude Agent SDK: subagents are "separate agent instances", "each subagent runs in its own conversation"; "Subagents can also spawn subagents of their own" (depth default 3, concurrency 20); `maxTurns` -> partial output -> resumable; `Workflow` tool "moves the orchestration into a script the runtime executes outside the conversation context" for "dozens to hundreds of agents". — [Claude Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)
- Managed Agents: subagents each "work in its own session thread", threads persist for follow-ups; dynamic workflows: "Claude writes a program to orchestrate agents without Claude's direct involvement"; "You follow the run by its phases and can read each of its threads"; delegation one level deep, 25 child threads. — [Managed Agents multiagent](https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration)
- OpenAI Agents SDK: nested runs via `Agent.as_tool()` and handoffs; approvals inside nested runs "surface on the outer run". Under Temporal, "orchestration stays in the workflow" and each model call is an activity. — [OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/); [temporalio/ai-integrations](https://github.com/temporalio/ai-integrations/tree/main/python/openai_agents)
- smolagents: sub-agents via `managed_agents`, called "like any other tool". — [smolagents reference](https://huggingface.co/docs/smolagents/reference/agents.md)

### Inferences
- "Agent as node" is now table stakes in graph frameworks, but the *checkpoint granularity* differs: MAF checkpoints the agent's session inside the executor; LangGraph checkpoints subgraph state per superstep; ADK skips already-successful sub-nodes on resume; CrewAI's crew-inside-flow is opaque to flow persistence (the crew run is one step). For hour-long coding sessions the intra-session checkpoint (Managed Agents event log + sandbox, Agent SDK transcript files, Temporal activity history) is what actually survives a crash.

### Gaps
- No primary source quantifying how LangGraph Agent Server handles multi-hour node executions (timeouts, leases).
- Could not confirm a maintained Deep Agents TypeScript parity for subagents.

---

## KQ7. Known criticisms: graph determinism vs agent autonomy, cost/token budgeting, long-running engineering tasks (code changes, PR creation)

### Takeaway
Practitioner critiques cluster on: (1) explicit graphs are the wrong layer for open-ended reasoning ("The two problems share a word. They do not share an architecture"), (2) abstraction/API churn and multiplied state copies make debugging harder, (3) framework loop limits (`recursion_limit`, `max_iter`) are step counters mis-used as cost caps, (4) role-play multi-agent frameworks (CrewAI) compound tokens, (5) checkpointing is "one layer" — workers dying, redeploys, throttling, long human waits and cost controls are the harder production problems. Counter-evidence: Google reports ~50% token savings when a workflow graph replaces an autonomous loop on a narrow task. No rigorous source specifically on LangGraph for multi-hour PR-producing coding agents was found.

### Cited Findings
- Eric Barroca (Vertesia, 2026-05-19): LangGraph is "one of the most technically sophisticated open-source agent frameworks", but explicit graph control is the wrong layer for open-ended reasoning where "the reasoning path is part of the problem"; business process control needs "typed state, guards, retries, and audit trails" in a separate process engine where agents "choose from declared exits, never invent them"; recommends investing in the harness around the agent loop; checkpointing is "one layer inside a broader production system"; production concerns listed: "workers dying mid-run, redeploys, provider throttling, long human waits, cost controls, and cross-process observability". — [ebarroca.substack.com](https://ebarroca.substack.com/p/langgraph-control-flow-is-not-the-main-problem)
- braindetox (2026-07-21): a NestJS workflow engine reached "8,692 lines of code spread across three executor layers" while core LLM calls were <100 lines and the graph had "just one node"; session state checkpointed in both LangGraph `MemorySaver` and a custom Postgres service, spread across five places with no authoritative copy; complaints: abstraction layers, control flow hidden behind callbacks/memory wrappers, API churn, "Wrapper overhead can reach a second or more per request"; cites Anthropic's "Building Effective Agents" that abstraction "can obscure the underlying prompts and responses, making debugging harder"; community drift toward direct API calls, SmolAgents (~1,000 lines), PocketFlow (~100 lines), a ~60-line agent loop. — [braindetox.kr](https://braindetox.kr/en/posts/langgraph_complexity_lesson_2026.html)
- Podcast on LangGraph "graph engineering": once planning/delegation/search/synthesis are open-ended, "hardcoding the path can become premature structure"; teams "start drawing boxes because boxes feel adult, and if the graph does not expose decisions, approvals, retries, state, and receipts, it is just workflow cosplay". — [transcript](https://sthbxxuzklalnaiaeagd.supabase.co/storage/v1/object/public/exploring-next-audio/episodes/3-years-of-graph-engineering-with-langgraph-1784737435422.txt)
- arXiv preprint (63 budget-overrun incidents, not peer-reviewed): "LangGraph's recursion_limit, CrewAI's max_iter, and AutoGen's max_consecutive_auto_reply are structural step counters, not dollar-cap mechanisms. The catalog shows operators do mis-deploy them as cost proxies in production"; mitigation suggested at gateway level (e.g. LiteLLM proxy budgets). — [arXiv 2606.04056](https://arxiv.org/pdf/2606.04056)
- Token overhead comparisons (directional, methodology unclear): CrewAI "up to 3x the tokens of LangGraph"; one run reports CrewAI 4120 vs LangGraph 2350 median tokens/task with more hard failures; explanation: "every agent turn includes the conversation transcript so far"; LangGraph praised for forcing "hard, structural boundaries within the state graph" but warned about "infinite loops in state machines if no guard counter" and "1 to 2 weeks to get fluent". — [pasqualepillitteri benchmark](https://pasqualepillitteri.it/en/news/3095/claude-agent-sdk-vs-langgraph-vs-crewai-benchmark-2026-en); [dailyaiworld](https://dailyaiworld.com/workflow/langgraph-crewai-openai-sdk-97-wins-token-cut-production); [eastgate comparison](https://eastgate-software.com/ai-agent-frameworks-comparison/)
- Google's side-by-side (reported 2026-07-01, labelled "illustrative"): same refund task 2,265 tokens under an ADK 2.0 workflow vs 5,152 under an autonomous agent ("~50%"). — [ecorpit](https://ecorpit.com/adk-2-0-workflows-vs-autonomous-agents-guide-2026/)
- LangGraph's own durability caveat: crash-resume "requires Postgres, sync mode, and idempotent nodes" and "does not poll, alert, or self-heal". — [groundy.com](https://groundy.com/articles/langgraph-1-2-0-makes-error-handler-resume-crash-durable-with-conditions/)
- Visual builders: "once you need loops, dynamic fan-out, or complex error handling, you hit the limits of visual design pretty quickly"; OpenAI Agent Builder shutting down 2026-11-30. — [team400.ai](https://team400.ai/blog/2026-03-openai-agent-builder-visual-workflows); [OpenAI Agent Builder docs](https://developers.openai.com/api/docs/guides/agent-builder)
- Anthropic's own guidance referenced by Managed Agents docs: "when to use multiagent systems (and when not to)". — [Managed Agents multiagent](https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration)
- Anthropic's Agent SDK docs acknowledge cost growth: "a subagent can spawn subagents of its own, so one prompt can grow into a tree of agents" — hence depth/concurrency/`maxBudgetUsd` caps; "Claude Opus 5 delegates to subagents more readily than earlier models". — [Claude Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)
- Managed Agents docs on dynamic workflows: "Every agent in a run uses tokens, so set a session budget to cap the session's spend, runs included." — [Managed Agents multiagent](https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration)

### Inferences
- The critiques converge on a design principle relevant to a product: put *deterministic* things (approvals, retries, budgets, audit) in the graph/process layer and let the *agent session* own its reasoning loop as an opaque node. LangGraph can do this (subgraph/Deep Agent as node) but its idiom encourages fine-grained nodes, which is where the "workflow cosplay"/complexity complaints come from. ADK 2.0 and MAF explicitly design for "agent as node" and MAF additionally exposes "workflow as agent".
- Cost control remains the weakest spot in graph frameworks; only Anthropic's runtime (and gateways like LiteLLM) enforce dollar budgets.

### Gaps
- No peer-reviewed or vendor-neutral benchmark of LangGraph vs harness-style agents on multi-hour coding tasks with PR creation was found; the long-running engineering critique is inferred from general production concerns.
- Token benchmarks conflict and mostly come from vendor-adjacent blogs.
