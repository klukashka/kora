# Classic data/workflow orchestrators and "agentic" pipelines (Airflow 3.x, Prefect 3, Dagster, Flyte 2/Union, Kestra, Temporal) — state as of 2026-10-14

Scope note: all facts below come from official docs/blogs or vendor posts fetched 2026-10-14. Version anchors found during research: Apache Airflow 3.3.0 released 2026-07-06 (docs current at 3.3.1/3.3.2); `apache-airflow-providers-common-ai` 0.1.0 announced 2026-04-14, 0.7.0+ required for Airflow 3.3 state store; Prefect docs "v3"; Dagster docs "Latest (1.13.26)"; Flyte 2 SDK on PyPI up to 2.7.2; Kestra docs "Latest (2.0)", `plugin-ai` 2.3.2 dated 2026-10-14; Temporal OpenAI Agents SDK integration in public preview since July 2025. A major landscape event: Prefect announced on 2026-07-13 that it has agreed to acquire Dagster Labs (see last section).

---

## KQ1. Python DSL idioms: how each orchestrator describes DAGs/flows in code, and which idioms are worth mirroring

### Takeaway
Two dominant styles exist: (1) Airflow's decorator-plus-bitshift style (`@dag`/`@task`, `a >> b >> [c, d]`, `default_args` inheritance, `TaskGroup`, `.partial().expand()`), and (2) "workflow-as-plain-Python-function" style (Prefect `@flow`/`@task` with `.submit()`/futures; Flyte 2 `@env.task` where tasks simply `await` other tasks and there is no `@workflow` decorator). Dagster is the asset-centric outlier (`@asset`, deps via function parameters/`deps=[...]`), Kestra is YAML-only, and Temporal is imperative code (`@workflow.defn` + `@activity.defn`) with no static graph at all.

### Cited Findings

**Airflow 3.x (Task SDK, `airflow.sdk`)**
- Airflow 3.3 examples import from the Task SDK namespace: `from airflow.sdk import dag, task` and `from airflow.sdk import Asset, task`; multi-language support (AIP-108, experimental) lets Java/Go implement task logic while "Python still declares the DAG shape using `@task.stub`" — [Airflow 3.3.0 blog](https://airflow.apache.org/blog/airflow-3.3.0/)
- Dependencies are set with `>>` and `<<` or `set_upstream`/`set_downstream`; the docs recommend the bitshift form, e.g. `first_task >> second_task >> [third_task, fourth_task]` — [Airflow Tasks docs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html)
- Dynamic task mapping is enabled "via the `expand()` method on tasks"; only keyword arguments may be passed to `expand()`; static params go through `partial()`, e.g. `add.partial(y=10).expand(x=[1, 2, 3])`; mapped inputs arrive as lazy proxy objects (wrap in `list()` to force) — [Airflow Task SDK dynamic task mapping](https://airflow.apache.org/docs/task-sdk/stable/dynamic-task-mapping.html)
- Per-task executor selection: "Tasks select an executor via the `executor` parameter on operators, or via `default_args` at the Dag level" — [Airflow Executor docs 3.3.1](https://airflow.apache.org/docs/apache-airflow/3.3.1/core-concepts/executor/index.html)
- Per-task executor-specific config via `executor_config={"KubernetesExecutor": {"image": "myCustomDockerImage"}}` — [Airflow Tasks docs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html)
- The HITL tutorial shows list-based fan-in/fan-out wiring: `[wait_for_input, wait_for_option, ...] >> valid_input_and_options >> choose_a_branch_to_run >> [task_1(), task_2(), task_3()]` — [Airflow HITL tutorial 3.1.3](https://airflow.apache.org/docs/apache-airflow/3.1.3/tutorial/hitl.html)
- The AI decorators extend the same `@task` namespace: `@task.llm(...)`, `@task.agent(...)`, `@task.llm_branch(...)` (see KQ8 for snippets) — [Airflow common-ai provider blog](https://airflow.apache.org/blog/common-ai-provider/)
- Event-driven scheduling: an `AssetWatcher` "watches one or more triggers for events"; triggers inheriting `BaseEventTrigger` can be used in watchers; the payload is attached to the `AssetEvent` `extra` dict; `MessageQueueTrigger` works with SQS and Kafka — [Astronomer event-driven scheduling guide](https://www.astronomer.io/docs/learn/airflow-3/airflow-event-driven-scheduling); [Airflow event-scheduling docs 3.3.2](https://airflow.apache.org/docs/apache-airflow/3.3.2/authoring-and-scheduling/event-scheduling.html)
- Airflow 3.3 also adds an experimental "Dag Results" feature: a `@result` decorator and a `/wait` endpoint that streams NDJSON — [Airflow 3.3.0 blog](https://airflow.apache.org/blog/airflow-3.3.0/)

**Prefect 3**
- `@task` params documented: `name`, `description`, `tags`, `timeout_seconds`, `cache_key_fn`, `cache_policy` (`INPUTS`, `TASK_SOURCE`, `RUN_ID`, `FLOW_PARAMETERS`, `NO_CACHE`, combinable with `+`), `cache_expiration`, `retries`, `retry_delay_seconds`, `log_prints`; `task_run_name` accepts a format string such as `"hello-{name}-on-{date:%A}"` or a callable — [Prefect write-tasks](https://docs.prefect.io/v3/develop/write-tasks.md)
- Concurrency: calling `.submit()` on a task inside a flow runs it through the flow's task runner; default is `ThreadPoolTaskRunner`; `@flow(task_runner=ProcessPoolTaskRunner())` is the alternative shown — [Prefect write-tasks](https://docs.prefect.io/v3/develop/write-tasks.md)

**Dagster (1.13.x)**
- `@dg.asset(deps=[daily_sales], group_name="sales")`; upstream can also be wired via `ins={"upstream_asset": dg.AssetIn(key_prefix=[...])}`; `@dg.multi_asset(specs=[dg.AssetSpec("asset_one"), dg.AssetSpec("asset_two")])`; `@graph_asset` composes ops (`return step_two(step_one())`); `code_version="1"` marks changes; `key_prefix=["one","two","three"]` for multi-part keys — [Dagster defining assets](https://dagster.io/docs/guides/build/assets/defining-assets)
- With I/O managers, "Upstream assets are loaded by naming the upstream asset as a function parameter"; outputs are stored by returning from the asset function; without an I/O manager you use `@dg.asset(deps=[...])` and do I/O inside — [Dagster I/O managers](https://dagster.io/docs/guides/build/io-managers)
- Jobs choose executors via `@dg.job(executor_def=dg.multiprocess_executor)` or `graph.to_job(executor_def=...)`; a code-location default via `@dg.definitions` returning `dg.Definitions(executor=dg.multiprocess_executor)` — [Dagster run executors](https://dagster.io/docs/guides/operate/run-executors)

**Flyte 2 / Union**
- `TaskEnvironment` defines image, CPU/GPU, secrets; `@env.task` "Turns any Python function into a remotely-executed step"; "Tasks can `await` other tasks, so the calling task acts as the workflow. No separate workflow decorator is needed"; `@flyte.trace` marks helper functions for fine-grained observability/caching (spans with checkpointed inputs/outputs); `flyte.Trigger` for schedules; `AppEnvironment` for webhooks/long-running apps — [Flyte 2 Build an agent](https://www.union.ai/docs/v2/flyte/user-guide/build-agent)
- Config layering: `TaskEnvironment` defaults -> `@env.task(...)` overrides -> `task.override(...)` at call time; `TaskEnvironment(name, depends_on, image, resources, env_vars, secrets, cache, reusable, plugin_config, queue)`; migration maps Flyte 1 `@task(cache=True, retries=3, timeout=3600)` to `TaskEnvironment(cache="auto")` + `@env.task(retries=3, timeout=3600)`; "Flyte 2 also has no `@workflow` decorator: workflows are tasks that call other tasks"; run via `flyte.run(main, name="World")` / `flyte run` CLI after `flyte.init_from_config()` — [Union docs: TaskEnvironment](https://www.union.ai/docs/v2/flyte/api-reference/flyte-sdk/flyte/taskenvironment/); [Union migration: tasks and workflows](https://docs.union.ai/docs/v2/byoc/api-reference/migration/tasks-and-workflows)

**Kestra (2.0)**
- Flows are YAML: `id`, `namespace`, `inputs`, `tasks: - id/type: io.kestra.plugin...`; task outputs referenced as `{{ outputs.<task_id>.<field> }}`, e.g. `{{ outputs.multilingual_agent.textOutput }}` — [Kestra AI agents docs](https://kestra.io/docs/ai-tools/ai-agents)

**Temporal**
- Workflows are classes: `@workflow.defn class HumanInTheLoopWorkflow: def __init__(self): self.current_decision = None ...`; signal handlers `@workflow.signal async def approval_decision(self, decision)`; the `@workflow.run` method drives the logic — [Temporal HITL cookbook](https://docs.temporal.io/ai/cookbook/human-in-the-loop-python.md)
- In the OpenAI Agents SDK integration, tools are Activities wrapped with `openai_agents.workflow.activity_as_tool(...)`, which "generates OpenAI-compatible tool schemas from Activity function signatures"; the `@workflow.run` method calls `Runner.run(agent, prompt)` — [Temporal OpenAI Agents SDK cookbook](https://docs.temporal.io/ai/cookbook/openai-agents-sdk-python)

### Inferences
- The idioms with the widest recognition in Python data engineering are Airflow's: `@dag`/`@task` decorators, `>>` for edges, `default_args` propagated to all tasks, `TaskGroup` for nesting, and `.partial(...).expand(...)` for fan-out. A new framework that mirrors these will feel familiar to the largest user base; the Airflow team itself reused the `@task.<flavor>` namespace for `llm`/`agent`/`llm_branch`, which suggests "agent as a task flavor" is a natural extension point.
- The "no workflow decorator, just `await` tasks" style (Flyte 2) and Temporal's imperative style are better matched to agent loops whose shape is unknown ahead of time; a hybrid (declarative skeleton + imperative agent step) is what Airflow 3.3 + common-ai effectively offers.
- Dagster's asset graph is the least natural fit for agent loops (its own docs show LLM features at the ops/UI-agent level, not as a graph primitive — see KQ8).

### Gaps
- I did not fetch the Airflow `TaskGroup`/`default_args` reference page explicitly; the `default_args` inheritance claim above is cited only via the executor docs. Trigger-rule list and `chain`/`cross_downstream` were not on the fetched page.
- Prefect `.map()`, `wait_for`, `DaskTaskRunner`/`RayTaskRunner`, `retry_condition_fn`, and list/exponential `retry_delay_seconds` were not on the fetched page (the docs mention they exist elsewhere).

---

## KQ2. How each serializes the graph (JSON/YAML, DB)

### Takeaway
Airflow serializes every parsed DAG to JSON in the metadata DB (`SerializedDagModel`), and since 3.1 that JSON carries a `__version` field and a versioned Task-SDK/server contract; Kestra's source of truth is YAML; Dagster, Prefect and Flyte register definitions/deployments with their API/server rather than serializing a static graph; Temporal has no graph at all — its durable artifact is the per-execution Event History.

### Cited Findings
- Airflow: the scheduler's `DagFileProcessorProcess` parses DAG files, serializes them to JSON and saves them as the `SerializedDagModel`; the webserver reads/deserializes these instead of parsing files; the scheduler uses serialized DAGs, not files, for scheduling decisions; source can be stored in `DagCode`; rendered templates in `RenderedTaskInstanceFields`; config `[core] min_serialized_dag_update_interval = 30`, `compress_serialized_dags = False` (when True the dependencies view is disabled), `num_dag_runs_to_retain_rendered_fields = 30`; "Dag Serialization is strictly required and can not be turned off from Airflow 2.0+"; in 3.1+ the serialized JSON carries `__version` and optional `client_defaults`, and there is a versioned contract between Task SDKs and server components — [Airflow DAG serialization docs](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/dag-serialization.html)
- Airflow 3.3 DAG/bundle versioning: runs can use latest or original bundle version for clear/rerun/backfill, resolved via request parameter, Dag-level setting, or config; clear/rerun default to original, backfills default to latest — [Airflow 3.3.0 blog](https://airflow.apache.org/blog/airflow-3.3.0/)
- Airflow `supports_pickling` executor attribute means "Whether Dags can be read from the database instead of the filesystem" — [Airflow Executor docs 3.3.1](https://airflow.apache.org/docs/apache-airflow/3.3.1/core-concepts/executor/index.html)
- Prefect: "A deployment definition is registered with the Prefect API as a deployment, and its flow code lives in remote storage"; the deployment targets a work pool — [Prefect work pools](https://docs.prefect.io/v3/concepts/work-pools.md)
- Kestra flows are YAML documents (`id`, `namespace`, `tasks`) — [Kestra AI agents docs](https://kestra.io/docs/ai-tools/ai-agents); [Kestra Pause task](https://kestra.io/plugins/core/flow/io.kestra.plugin.core.flow.pause)
- Temporal: for training jobs, "checkpointing and recovery come from Temporal's Event History rather than custom orchestration code" — [Temporal AI docs](https://docs.temporal.io/ai)

### Inferences
- Airflow is the only one of the six with a documented, versioned, JSON-serialized static graph that the scheduler consumes without re-importing user code — the closest precedent for a "graph serialized to JSON/DB" design.
- Temporal's and Flyte 2's models show the alternative: persist execution (event log / traced checkpoints), not the graph.

### Gaps
- Dagster definition serialization (the "external repository" / code-location snapshot protocol) and Flyte 2 task/run registration formats were not researched here.
- Which Python objects are non-serializable in Airflow (callables etc.) is not stated on the fetched page.

---

## KQ3. Runtime/executor abstraction and the plug-in protocol for a custom executor

### Takeaway
All five "classic" orchestrators have a pluggable compute layer with a small documented base class: Airflow `BaseExecutor` (mandatory `sync`, `execute_async`, `_process_workloads`), Prefect `BaseWorker` (`type`, `job_configuration`, async `run(flow_run, configuration, task_status) -> BaseWorkerResult`), Dagster `RunLauncher` (abstract `launch_run(context)`, `terminate(run_id) -> bool`) plus an in-worker `Executor` that is explicitly "not currently well-documented". Flyte 2 configures compute via `TaskEnvironment`/`plugin_config`/`queue` rather than a user-written executor. Temporal's unit of runtime is the Worker polling a Task Queue.

### Cited Findings

**Airflow 3.3.1**
- Executors "have a common API and are pluggable"; `LocalExecutor` (core, default; runs tasks in the scheduler process); queued/batch: `CeleryExecutor` (celery provider), `BatchExecutor` (amazon), `EdgeExecutor` (edge3 provider); containerized: `KubernetesExecutor` (cncf-kubernetes), `EcsExecutor` (amazon) — [Airflow Executor docs 3.3.1](https://airflow.apache.org/docs/apache-airflow/3.3.1/core-concepts/executor/index.html)
- Mandatory methods for a custom executor: `sync` (called during heartbeats to update known task states / run queued tasks), `execute_async` (run a workload asynchronously — enqueue externally or run directly like LocalExecutor), `_process_workloads` (process workloads queued via `queue_workload`). Optional: `start`, `end`, `terminate`, `try_adopt_task_instances`, `get_cli_commands` (static method returning a `GroupCommand`), `get_task_log`. Non-override helpers: `heartbeat`, `queue_workload`, `get_event_buffer`, `has_task`, `send_callback`. Compatibility attributes: `supports_pickling`, `sentry_integration`, `is_local`, `is_single_threaded`, `is_production`, `serve_logs`. The workload example is an `ExecuteTask` with fields `token`, `ti` (`TaskInstanceDTO`), `dag_rel_path`, `bundle_info`, `log_path`, `type` — [Airflow Executor docs 3.3.1](https://airflow.apache.org/docs/apache-airflow/3.3.1/core-concepts/executor/index.html)
- Configuration: `[core] executor = my.custom.executor.module.ExecutorClass`; multi-executor as a comma list with aliases `alias:module.ClassName` (e.g. `LocalExecutor,short_name:my.custom.module.ExecutorClass`); first in list is default; two instances of the same class only in multi-team Airflow; a DAG referencing an unconfigured executor fails to parse; per-executor metrics `executor.open_slots/queued_slots/running_tasks` suffixed by class name. Version notes: multi-executor since 2.10.0; 3.0.0 removed `LocalKubernetesExecutor`/`CeleryKubernetesExecutor`; 3.2.0 introduced provider-level CLI commands for executors/auth managers — [Airflow Executor docs 3.3.1](https://airflow.apache.org/docs/apache-airflow/3.3.1/core-concepts/executor/index.html)
- Providers are plain Python packages with provider metadata (fields such as `auth-backends`, `auth-managers`, `bundles`, `config`, `connection-types`) — [Airflow create custom providers](https://airflow.apache.org/docs/apache-airflow-providers/howto/create-custom-providers.html)

**Prefect 3**
- Work pool types (Cloud): Process, AWS ECS, Azure Container Instances, Docker, Google Cloud Run, Cloud Run V2, Vertex AI, Kubernetes, push variants (Cloud Run, ECS, ACI, Modal), Coiled, Prefect Managed; self-hosted lacks push/Coiled/Managed. Three modes: hybrid (you run a worker that polls the pool and submits to your infra), push (serverless provider, no worker), managed (Prefect infra, no worker). Work queues have priority (1 = highest) and per-queue concurrency; READY if polled within 60s — [Prefect work pools](https://docs.prefect.io/v3/concepts/work-pools.md)
- Custom worker: subclass `BaseWorker` with required `type` and `job_configuration` (a `BaseJobConfiguration` subclass with common attrs `name`, `env`, `labels`, `command`, custom Pydantic fields, `json_schema_extra=dict(template=...)`, and a `prepare_for_flow_run` hook), optional `job_configuration_variables` (`BaseVariables`), and the required method `async def run(self, flow_run: FlowRun, configuration: BaseJobConfiguration, task_status: anyio.abc.TaskStatus = anyio.TASK_STATUS_IGNORED) -> BaseWorkerResult` (create infra, `task_status.started(<id>)`, monitor, return exit status). Registration via `prefect.collections` entry point so `prefect worker start --type` finds it — [Prefect developing a custom worker](https://docs.prefect.io/v3/advanced/developing-a-custom-worker.md)

**Dagster 1.13.26**
- Run launchers: `DefaultRunLauncher` (separate process per run on the code-location node), `K8sRunLauncher`, `ecs.EcsRunLauncher`, `DockerRunLauncher`, `CeleryK8sRunLauncher`. "The run launcher only determines the behavior of the run worker"; once execution begins, the executor — "an in-memory abstraction in the run worker process" — takes over. Custom launchers subclass `RunLauncher` — [Dagster run launcher](https://dagster.io/docs/deployment/run-launcher)
- `RunLauncher` abstract methods: `launch_run(context)` (run must already exist in the instance in STARTING state; invoked via `DagsterInstance.launch_run()`), `terminate(run_id) -> bool`; optional `dispose()`, `join(timeout=30)`, opt-in health checks `supports_check_run_worker_health` / `check_run_worker_health` returning `CheckRunHealthResult` with `WorkerStatus` (RUNNING, NOT_FOUND, FAILED, SUCCESS, UNKNOWN); registered in `dagster.yaml` under `run_launcher` with `module`/`class`/`config` — [Dagster launcher base source](https://docs.dagster.io/_modules/dagster/_core/launcher/base); [dagster-celery-k8s docs](https://docs.dagster.io/api/libraries/dagster-celery-k8s)
- Executors: `in_process_executor`, `multiprocess_executor`, `dask_executor`, `celery_executor`, `docker_executor`, `k8s_job_executor`, `celery_k8s_job_executor`, `ecs.ecs_executor`, `celery_docker_executor`; default is `multi_or_in_process_executor`; the executor system is pluggable but "not currently well-documented" and internal APIs are still changing — [Dagster run executors](https://dagster.io/docs/guides/operate/run-executors)

**Flyte 2**
- Compute is declared on `TaskEnvironment(..., image, resources, plugin_config, queue, reusable, ...)`; tools/tasks are "dispatched durably on the cluster" in code mode — [Union TaskEnvironment](https://www.union.ai/docs/v2/flyte/api-reference/flyte-sdk/flyte/taskenvironment/); [Flyte Agent API](https://www.union.ai/docs/v2/flyte/api-reference/flyte-sdk/packages/flyte.ai.agents/agent/)
- Availability caveat: "Flyte 2 is available today for local execution - distributed execution coming to open source soon"; hosted version is described as a preview — [Union docs](https://www.union.ai/docs/)

**Temporal**
- Worker uses `OpenAIAgentsPlugin` with `ModelActivityParameters` (e.g. `start_to_close_timeout` 30s for model calls) and listens on a task queue (`hello-world-openai-agent-task-queue`); the starter uses the same plugin so config matches — [Temporal OpenAI Agents SDK cookbook](https://docs.temporal.io/ai/cookbook/openai-agents-sdk-python)
- Task Queue Priority and Fairness "for running urgent work ahead of bulk work and preventing tenant starvation" — [Temporal AI docs](https://docs.temporal.io/ai)

### Inferences
- The minimal "executor protocol" common to Airflow/Prefect/Dagster is: (a) a submit/launch call taking a serialized workload; (b) a poll/sync or health-check call; (c) terminate/kill; (d) optional log retrieval and CLI commands; (e) declarative capability flags. Airflow's `ExecuteTask` workload DTO (token + task-instance DTO + bundle info + log path) is the clearest example of decoupling the executor from the task's code.
- Prefect's design (config schema as Pydantic + base job template editable in UI) is the most "operator-friendly" plug-in protocol; Airflow's is the most scheduler-integrated.

### Gaps
- Dagster `Executor` base-class interface (`execute(plan_context, execution_plan)`) is not documented on the fetched page; Dagster itself says the API is unstable.
- Prefect `kill_infrastructure` signature not on the fetched page.
- Airflow provider metadata `executors` field was not confirmed in the fetched results.
- Flyte 2 open-source distributed executor/backend plugin interface was not found.

---

## KQ4. Human-in-the-loop / approval / pause primitives

### Takeaway
Airflow 3.1 ships four HITL operators (`HITLOperator`, `ApprovalOperator`, `HITLBranchOperator`, `HITLEntryOperator`) and 3.3 adds a dedicated `awaiting_input` task state that holds neither worker nor triggerer; Prefect has `pause_flow_run`/`suspend_flow_run` with typed `wait_for_input` (`RunInput`), plus `send_input`/`receive_input`; Kestra has a core `Pause` task with `onResume` typed inputs and a `/executions/{id}/resume` endpoint; Temporal uses Signals + `workflow.wait_condition(..., timeout=)`; Flyte 2's `Agent` has per-tool `requires_approval=True` resolved through Flyte "conditions" signaled from UI/CLI; Dagster has no native approval primitive found in official docs.

### Cited Findings

**Airflow (3.1+, changed in 3.3)**
- "Added in version 3.1". Classes: `HITLEntryOperator` (free text via `params`), `HITLOperator` (`options`, `multiple=True` for multi-select), `ApprovalOperator` (fixed Approve/Reject; supports `body`, `assigned_users`), `HITLBranchOperator` (options are downstream task IDs). Params seen: `subject`, `body`, `options`, `multiple`, `defaults`, `params={"information": Param("", type="string")}`, `assigned_users=[{"id": "admin", "name": "admin"}]` ("ONLY the users within this list will be allowed to respond"), `notifiers=[...]`, `execution_timeout`, `on_success_callback`, `on_failure_callback`. Example: `ApprovalOperator(task_id=..., subject="Are the following input and options valid?", body="""...jinja pulling upstream XComs...""", defaults="Reject", execution_timeout=datetime.timedelta(minutes=1), notifiers=[hitl_request_callback], assigned_users=[{"id": "admin", "name": "admin"}])`. Responses are submitted in the UI via the task's "Required Actions" tab; results are read from XCom as `["chosen_options"]` (option/approval) and `["params_input"]["information"]` (entry). Notifiers get `notify(context)` when a request is created; `HITLOperator.generate_link_to_ui_from_context(context, base_url=None, ...)` builds a response link; templates like `{{ task.subject }}`, `{{ task.options }}`, `{{ task.defaults }}` — [Airflow HITL tutorial 3.1.3](https://airflow.apache.org/docs/apache-airflow/3.1.3/tutorial/hitl.html)
- Astronomer summary: pending actions also appear under Browse -> Required Actions or via the REST API; in 3.1/3.2 the operators are deferrable (async trigger in the Triggerer); in 3.3+ they enter `awaiting_input` which "doesn't require a running triggerer or worker" — [Astronomer HITL guide](https://www.astronomer.io/docs/learn/airflow-3/airflow-human-in-the-loop)
- 3.3.0 release notes: "Add awaiting_input task state for Human-in-the-Loop, running off the triggerer (#68028)"; tasks resume on a human response or the scheduler's response-timeout sweep; the Tasks page lists `awaiting_input` among 14 task states ("uses neither a worker slot nor the triggerer") — [Airflow 3.3.0 release notes](https://airflow.apache.org/docs/apache-airflow/3.3.0/release_notes.html); [Airflow Tasks docs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html)
- Security: CVE-2026-67260 — the `awaiting_input` sweep deserialized `next_kwargs` without an allow-list, allowing a DAG author to import arbitrary modules in the scheduler; fixed in 3.3.1 — [OpenCVE CVE-2026-67260](https://app.opencve.io/cve/CVE-2026-67260)
- Agent-level HITL in the common-ai provider: `AgentOperator`/`LLMOperator` accept `require_approval`, `approval_timeout`, `allow_modifications`, `enable_hitl_review`, `max_hitl_iterations`, `hitl_timeout` — [Airflow common-ai provider blog](https://airflow.apache.org/blog/common-ai-provider/); limitation: "`durable=True` and `enable_hitl_review=True` cannot be used together" — [Pydantic AI Airflow durable execution](https://pydantic.dev/docs/ai/integrations/durable_execution/airflow/)

**Prefect 3**
- `pause_flow_run` and `suspend_flow_run` both take `wait_for_input`, which accepts a built-in type (`int`, `str`, `List[int]`), a `pydantic.BaseModel` subclass, or a `prefect.input.RunInput` subclass; e.g. `user = pause_flow_run(wait_for_input=str)`; `await suspend_flow_run(timeout=10000)`; suspend "exits the flow and tears down infrastructure". `class UserInput(RunInput): name: str; age: int`; `UserInput.with_initial_data(name="anonymous", description="<markdown shown in UI>")`; built-in types create a model with a single field `value`, so programmatic resume is `resume_flow_run(flow_run_id, run_input={"value": "Alice"})`. Validation via `model_validator(mode="after")` runs after resume, not in the UI (loop with `while` + re-pause to retry). Runtime messaging: `receive_input(str, timeout=None, with_metadata=True, poll_interval=0.1)` async iterator with `exclude_keys`; `send_input(value, flow_run_id=...)`; `name_input.respond(...)`. UI: Resume button renders a form — [Prefect interactive workflows](https://docs.prefect.io/v3/advanced/interactive.md)
- The `wait_for_input` parameter is flagged experimental ("may change without warning"); "If the flow is resumed without providing the input, the flow will fail" — [Prefect pause/resume search summary citing docs.prefect.io/v3/develop/pause-resume](https://docs.prefect.io/v3/develop/pause-resume) and [Prefect flow_runs API ref](https://docs.prefect.io/v3/api-ref/python/prefect-flow_runs)

**Kestra 2.0**
- `io.kestra.plugin.core.flow.Pause`: `pauseDuration` (ISO-8601, e.g. `PT1H`; if unset and no timeout, waits until resumed), `behavior` on expiry (`RESUME` default, `WARN`, `FAIL`, `CANCEL`), `onResume` typed inputs that users fill before resuming (read downstream as `outputs.<task_id>.onResume`), outputs `onResume` and `resumed` (who/when/status). Resume via UI, `/executions/{id}/resume` API, or automatically after `pauseDuration`. Verbatim example: `- id: pause  type: io.kestra.plugin.core.flow.Pause` between a `before_approval` task and a `run_post_approval` shell task — [Kestra Pause task](https://kestra.io/plugins/core/flow/io.kestra.plugin.core.flow.pause)

**Temporal**
- HITL cookbook: workflow state `self.current_decision: Optional[ApprovalDecision] = None`, `self.pending_request_id`; `@workflow.signal async def approval_decision(self, decision: ApprovalDecision)` stores the decision only if `decision.request_id == self.pending_request_id`; wait with `await workflow.wait_condition(lambda: self.current_decision is not None, timeout=timedelta(seconds=timeout_seconds))`; default approval timeout "5 minutes (default)"; the workflow "can wait for hours, days, or indefinitely without consuming compute"; approval request surfaced by a notification activity; client sends via `uv run send_approval.py <workflow-id> <request-id> approve "Looks good"` — [Temporal HITL cookbook](https://docs.temporal.io/ai/cookbook/human-in-the-loop-python.md)
- Approval is a named design pattern: "human-in-the-loop Workflows that use Signals to capture approval decisions"; Entity Workflow pattern driven by Signals and Updates — [Temporal AI docs](https://docs.temporal.io/ai); [Temporal approval pattern](https://docs.temporal.io/design-patterns/approval.md)

**Flyte 2**
- `Agent(approval_callback: ApprovalCallback = _condition_approval, ...)`: tools marked `requires_approval=True` pause the run; the default callback "registers a condition with `flyte.new_condition` and blocks until a human signals it, via the UI, `flyte signal condition`, or `flyte.remote.Condition.signal`. Outside a Flyte task context, it denies the call"; in `code_mode` per-tool approval is not enforced — [Flyte Agent API](https://www.union.ai/docs/v2/flyte/api-reference/flyte-sdk/packages/flyte.ai.agents/agent/)

**Dagster**
- No native approval/pause primitive was found in official docs or search results; results only surfaced other products' HITL docs — [search summary](https://docs.hatchet.run/guides/human-in-the-loop)

### Inferences
- Three distinct HITL shapes recur: (1) a gate task with typed form schema + timeout + default (Airflow HITL ops, Kestra Pause `onResume`), (2) a pause call inside imperative code returning a typed value (Prefect `pause_flow_run(wait_for_input=Model)`, Temporal signal + `wait_condition`), (3) per-tool approval inside the agent loop (Flyte `requires_approval`, Airflow common-ai `require_approval`/`enable_hitl_review`). A new framework would ideally support both the gate-task shape (for a serialized graph) and per-tool approval (inside an agent step).
- The Airflow 3.3 `awaiting_input` state is the most explicit "zero-compute while waiting" implementation among the classic DAG schedulers; Temporal and Flyte get this from their execution models.

### Gaps
- Airflow REST endpoints for HITL responses were not on the tutorial page.
- Prefect default pause timeout (commonly cited as 1 hour) and the `Paused`/`Suspended` state names were not confirmed on fetched pages.
- Whether Dagster+ offers an approval feature is unverified.

---

## KQ5. Retry / timeout / SLA semantics at task and sub-graph level

### Takeaway
All systems retry at the task/activity level with exponential backoff options; Airflow 3.3 adds pluggable retry policies (AIP-105) that map exception types to RETRY/FAIL, removed SLAs in 3.0 and replaced them with Deadline Alerts in 3.1; Prefect has flow-level and task-level `retries`/`timeout_seconds` with a documented caveat that thread-pool task timeouts cannot interrupt blocking calls; Dagster uses `RetryPolicy(max_retries, delay, backoff, jitter)`; Flyte 2 layers `retries`/`timeout` across environment, decorator and `override()`; Temporal retries Activities by default (unlimited attempts) and Workflows not at all.

### Cited Findings
- Airflow: `retries`, `retry_delay`, `retry_exponential_backoff`, `max_retry_delay` apply when no retry policy overrides them; 3.x retry policies map exception types to RETRY/FAIL/DEFAULT; `AirflowFailException` bypasses the policy; `execution_timeout` raises `AirflowTaskTimeout` and the task can retry; sensor `timeout` (reschedule mode only) raises `AirflowSensorTimeout` without retry; "SLAs were removed in Airflow 3.0 and replaced in 3.1 with Deadlines Alerts"; former "zombie" tasks are now handled by heartbeat timeout — [Airflow Tasks docs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html)
- Airflow 3.3: "Pluggable retry policies (AIP-105): Custom policies can decide whether and when a task retries" — [Airflow 3.3.0 blog](https://airflow.apache.org/blog/airflow-3.3.0/)
- Airflow XComs "are cleared on each retry, so they can't be used to persist state across task retries"; the 3.3 task state store "survives retries within the same Dag run" — [Airflow XCom docs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/xcoms.html); [Astronomer durable execution post](https://www.astronomer.io/blog/durable-execution-with-apache-airflow.md)
- Agent-level: `@task.agent(durable=True, retries=3)` — retries configured with `retries`/`retry_delay`; with `durable=True` each cache entry stores a request fingerprint, a matching fingerprint replays the cached result, a mismatch re-runs live; "a side-effecting tool runs at most once per completed step" — [Pydantic AI Airflow durable execution](https://pydantic.dev/docs/ai/integrations/durable_execution/airflow/)
- Prefect: task `retries`, `retry_delay_seconds`, `timeout_seconds` ("marked failed if exceeded"); flow-level `retries`, `retry_delay_seconds`, `timeout_seconds` ("Execution may continue until the next task is called"); timeout caveat: sync tasks via `.submit()` on `ThreadPoolTaskRunner` "cannot interrupt blocking operations" such as `time.sleep()`; async tasks are cancelled at the next `await` — [Prefect write-tasks](https://docs.prefect.io/v3/develop/write-tasks.md)
- Dagster: `dg.RetryPolicy(max_retries=5, delay=0.2, backoff=dg.Backoff.EXPONENTIAL, jitter=dg.Jitter.PLUS_MINUS)` shown on an op — [Dagster defining assets](https://dagster.io/docs/guides/build/assets/defining-assets)
- Flyte 2: `retries` is an int or `RetryStrategy`, default 0; `timeout` accepts `timedelta` or int; set on `@env.task(retries=3, timeout=60)` and overridable per call with `task.override(...)`; `cache` default `auto` — [Union task configuration](https://web-docs.union.ai/docs/v2/byoc/user-guide/task-configuration)
- Temporal RetryPolicy defaults: Initial Interval 1s, Backoff Coefficient 2.0, Maximum Interval 100x initial, Maximum Attempts unlimited (0 = unlimited, 1 = no retries), Non-Retryable Error types list; Activities retry by default, Workflow Executions do not; Schedule-To-Close recommended to bound total retry duration; Workflow Execution Timeout unlimited by default; `ApplicationError(..., non_retryable=True)` forces no retry — [Temporal retry policies](https://docs.temporal.io/encyclopedia/retry-policies)
- Temporal cookbook per-tool `start_to_close_timeout` 10s; model activity `start_to_close_timeout` 30s via `ModelActivityParameters` — [Temporal OpenAI Agents SDK cookbook](https://docs.temporal.io/ai/cookbook/openai-agents-sdk-python)
- Kestra Pause: `behavior` on expiry `RESUME|WARN|FAIL|CANCEL` — [Kestra Pause task](https://kestra.io/plugins/core/flow/io.kestra.plugin.core.flow.pause)

### Inferences
- For agent steps the critical semantic is "retry without re-paying completed LLM/tool calls"; only Airflow 3.3 (`durable=True` cache keyed by request fingerprint), Temporal (event-history replay) and Flyte 2 (`@flyte.trace` checkpoints) document that. Prefect/Dagster retries re-execute the whole task body.
- Sub-DAG-level retry (retrying a group) is not a first-class concept in any of the fetched docs; Prefect's flow-level `retries` and Temporal's child workflows are the closest analogues.

### Gaps
- Airflow TaskGroup-level retries/timeouts, Deadline Alerts API details, and Dagster asset-level `retry_policy` were not confirmed.
- Temporal activity timeout taxonomy (schedule_to_start, heartbeat) was not on the fetched page.
- Kestra flow-level retry (`retry:` blocks) was not researched.

---

## KQ6. Data passing between tasks: XCom, artifacts, assets/IO managers, typed outputs

### Takeaway
Airflow passes small JSON-ish values via XCom (`return_value` key, pluggable `BaseXCom` backends such as object storage) and in 3.3 adds a separate per-task-instance key-value **state store** (AIP-103) for retry-surviving state; Prefect returns values directly between Python calls and has a separate UI-oriented "artifacts" API (markdown/table/link/progress/image); Dagster routes asset outputs through I/O managers (`FilesystemIOManager` default; warehouse-specific ones); Flyte 2 uses typed task inputs/outputs plus `@flyte.trace` checkpointing; Kestra exposes task outputs via `{{ outputs.<id>.<field> }}`; Temporal passes values as activity results persisted in Event History.

### Cited Findings
- Airflow XCom: identified by `key` + `task_id` + `dag_id`; values any serializable object incl. `@dataclass`/`@attr.define`; "only designed for small amounts of data"; `do_xcom_push=True` default pushes to `return_value` (`BaseXCom.XCOM_RETURN_KEY`); `multiple_outputs=True` splits a returned dict; `xcom_pull` without `task_ids` pulls only from the current task (Airflow 2 searched all); cross-run pulls need `dag_id` + `run_id`; custom backend = subclass `BaseXCom`, override `serialize_value`/`deserialize_value` (and `purge`), select with `xcom_backend` config; object storage recommended for larger data via the common.io provider; verify loaded class via `airflow.sdk.execution_time.xcom.XCom.__name__` — [Airflow XCom docs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/xcoms.html)
- Airflow 3.3 task state store: KV scoped to (`dag_id`, `run_id`, `task_id`, `map_index`); methods `get/set/delete/clear` + async `aget/aset/adelete/aclear`; injected as `task_state_store` param or `context["task_state_store"]`; `set(..., retention=timedelta|NEVER_EXPIRE)`; asset state store `asset_state_store[ASSET]` readable across DAGs; pluggable `BaseStoreBackend`; `[state_store] max_value_storage_bytes` default 64 KB (warning but still stored), `[workers] state_store_backend` to offload — [Astronomer durable execution post](https://www.astronomer.io/blog/durable-execution-with-apache-airflow.md); [Pydantic AI Airflow durable execution](https://pydantic.dev/docs/ai/integrations/durable_execution/airflow/)
- Airflow common-ai: `@task.llm(output_type=Entities)` returns a typed Pydantic object so "Downstream tasks get structured data through XCom"; token usage is logged to the metadata DB — [Airflow common-ai provider blog](https://airflow.apache.org/blog/common-ai-provider/)
- Prefect artifacts: `create_link_artifact(key, link, link_text, description)`, `create_progress_artifact(progress, description)` + `update_progress_artifact(artifact_id, progress)` (updated in place, not versioned), `create_markdown_artifact(key, markdown, description)`, `create_table_artifact(key, table, description)` (list of lists / list of dicts / dict of lists), `create_image_artifact(image_url, description, key)`; a `key` enables versioning; read via `Artifact.get("key")`, CLI `prefect artifact inspect|ls|delete` — [Prefect artifacts](https://docs.prefect.io/v3/develop/artifacts.md)
- Dagster I/O managers: separate processing code from read/write code; upstream assets loaded by naming them as function parameters, outputs stored by returning; default `FilesystemIOManager` (pickle), `InMemoryIOManager` for tests, `s3.S3PickleIOManager`, ADLS2, `GCSPickleIOManager`, BigQuery/Snowflake/ClickHouse/DuckDB dataframe managers; poor fit for SQL-managed tables or data too large for memory — use `deps` instead — [Dagster I/O managers](https://dagster.io/docs/guides/build/io-managers)
- Flyte 2: `@flyte.trace` "Traced calls show as spans with inputs and outputs checkpointed"; `Agent.run(message, memory=None)` returns `AgentResult`; memory may be `None`, a `list[dict]`, or a `MemoryStore` ("keyed stores, optimistic concurrency") — [Flyte 2 Build an agent](https://www.union.ai/docs/v2/flyte/user-guide/build-agent); [Flyte Agent API](https://www.union.ai/docs/v2/flyte/api-reference/flyte-sdk/packages/flyte.ai.agents/agent/)
- Kestra: AIAgent outputs `textOutput`, `tokenUsage`; Pause outputs `onResume`, `resumed`; memory backends `KestraKVStore`, `Redis`, `PostgreSQL` with `messages` (default 10), `ttl` (PT1H), `memoryId` (default `{{labels.system.correlationId}}`), `drop` (`NEVER|BEFORE_TASKRUN|AFTER_TASKRUN`) — [Kestra AI agents docs](https://kestra.io/docs/ai-tools/ai-agents); [Kestra plugin-ai](https://kestra.io/plugins/plugin-ai)

### Inferences
- The clearest design precedent for agent state is Airflow 3.3's split: XCom for inter-task outputs (cleared on retry) vs. a task-scoped state store for intra-task checkpoints (survives retry). A new framework should separate "outputs" from "checkpoint/scratch state" the same way.
- Prefect artifacts are the only system with a first-class *human-facing* artifact type (markdown/table/progress) distinct from data outputs — useful for agent transcripts/summaries in a UI.

### Gaps
- Dagster custom I/O manager interface (`ConfigurableIOManager.handle_output/load_input`) was not on the fetched page.
- Prefect result persistence (`persist_result`, result storage blocks) was not researched.
- Flyte 2 typed-output serialization format was not researched.

---

## KQ7. Observability: UI, lineage, OpenLineage, logs

### Takeaway
Airflow has the most mature OpenLineage story (official provider, config-only); Dagster's OpenLineage support is a community package seeking maintainers, but Dagster's UI-level lineage is native to the asset graph and it has shipped an operational AI agent (Compass / Dagster+ AI); Flyte 2 traces every `reason()`/`act()` call and offers Grafana/OpenTelemetry integrations; Kestra exposes AI-specific metrics and optional Langfuse tracing; Temporal relies on Event History plus the OpenAI Traces dashboard in its cookbook. No OpenLineage integration was found for Prefect, Kestra or Flyte.

### Cited Findings
- Airflow OpenLineage provider "requires no changes to DAG files, only configuration telling it where to send events"; collects task input/output metadata — [Airflow OpenLineage provider guide](https://airflow.apache.org/docs/apache-airflow-providers-openlineage/stable/guides/structure.html)
- Airflow common-ai: using raw Pydantic AI from a plain `@task` "gives up durable execution, HITL review, and automatic tool logging"; token usage logged to the metadata DB — [Airflow common-ai provider blog](https://airflow.apache.org/blog/common-ai-provider/)
- Dagster OpenLineage is community-maintained (`dagster-openlineage`), emits asset-centric events with schema, column-lineage and data-quality facets; "new integration maintainers are needed"; two emission mechanisms (storage hook for self-hosted; sensor polling the event log for Dagster+) — [Dagster OpenLineage integration](https://docs.dagster.io/integrations/libraries/openlineage); [openlineage-dagster on PyPI](https://pypi.org/project/openlineage-dagster/)
- Dagster+ AI (preview, limited early access): "summarizing run failures, identifying root causes, and asking follow-up questions in a chat interface" — [Dagster+ AI docs](https://docs.dagster.io/guides/labs/dagster-ai); Compass: "an AI agent that now understands your Dagster+ environment", available in Dagster+ UI, Slack, Teams, drawing on runs, assets, failures, lineage, freshness; roadmap includes creating tickets, suggesting code changes, assisting pipeline authoring; aggregator dates the launch 2026-04-01 — [Dagster Compass blog](https://dagster.io/blog/closing-the-dataops-loop-why-we-built-compass-for-dagster); [Compass product page](https://compass.dagster.io/dagster-compass)
- Flyte 2: "each reason() and act() call is traced, so you see every LLM call, every tool invocation, and every intermediate result in the Flyte dashboard"; integrations: Grafana Agent Observability, OpenTelemetry, Sandboxing, an MCP server for AI assistants — [Flyte 2 Build an agent](https://www.union.ai/docs/v2/flyte/user-guide/build-agent)
- Kestra AIAgent: optional Langfuse tracing; counter metrics `ai.agent.tool.calls`, `ai.provider.calls`, `ai.embedding.store.calls`; `tokenUsage` output — [Kestra plugin-ai](https://kestra.io/plugins/plugin-ai); [Kestra AI agents docs](https://kestra.io/docs/ai-tools/ai-agents)
- Temporal cookbook: "Monitor runs in the OpenAI Traces dashboard" — [Temporal OpenAI Agents SDK cookbook](https://docs.temporal.io/ai/cookbook/openai-agents-sdk-python)
- Airflow executor `get_task_log` lets an executor add execution-environment logs to task logs; `serve_logs` attribute — [Airflow Executor docs 3.3.1](https://airflow.apache.org/docs/apache-airflow/3.3.1/core-concepts/executor/index.html)

### Inferences
- "Lineage" for agent pipelines is being re-interpreted as trace/span lineage (Flyte, Kestra/Langfuse, Temporal/OpenAI Traces) rather than dataset lineage (OpenLineage). A new framework targeting agents likely needs both an OpenTelemetry-style span model and an optional OpenLineage emitter.

### Gaps
- Prefect lineage/OpenLineage support: no sources found.
- Airflow UI support specifically for viewing agent tool-call logs (beyond "automatic tool logging") was not detailed.

---

## KQ8. First-class "LLM agent as a task" primitives — what they look like

### Takeaway
Four of six now ship a native primitive: Airflow (`@task.llm`, `@task.agent`, `@task.llm_branch` + `AgentOperator`/`LLMOperator`, provider `apache-airflow-providers-common-ai`, Pydantic-AI based, 0.1.0 on 2026-04-14), Kestra (`io.kestra.plugin.ai.agent.AIAgent` with tools/MCP/memory/RAG, `plugin-ai` 2.3.2), Flyte 2 (`flyte.ai.agents.Agent` harness with tools, MCP, skills, approval, code mode), and Temporal (framework integrations — OpenAI Agents SDK, LangGraph, Pydantic AI, Google ADK, etc. — rather than its own agent primitive). Prefect's ControlFlow was archived and merged into Marvin 3; Dagster has no agent-as-task primitive but has operational AI agents (Compass, Dagster+ AI).

### Cited Findings

**Airflow — `apache-airflow-providers-common-ai`**
- Announced Tue, Apr 14, 2026 (version 0.1.0; requires Airflow 3.0+; authors Kaxil Naik and Pavan Kumar Gopidesu; "0.x release, so breaking changes are possible"); built on Pydantic AI, 20+ model providers; connection types `pydanticai`, `pydanticai-azure`, `pydanticai-bedrock`, `pydanticai-vertex`; model strings like `openai:gpt-5` or `anthropic:claude-sonnet-4-20250514`; switching providers = changing the connection — [Airflow common-ai provider blog](https://airflow.apache.org/blog/common-ai-provider/)
- Verbatim `@task.llm`:
  ```python
  @task.llm(llm_conn_id="my_openai_conn", system_prompt="Extract named entities.", output_type=Entities)
  def extract(text: str):
      return f"Extract entities from: {text}"
  ```
  Verbatim `@task.agent`:
  ```python
  @task.agent(
      llm_conn_id="my_openai_conn",
      system_prompt="You are a SQL analyst. Use tools to answer questions with data.",
      toolsets=[SQLToolset(db_conn_id="postgres_default", allowed_tables=["customers", "orders"], max_rows=20)],
  )
  def analyze(question: str):
      return f"Answer this question about our data: {question}"
  ```
  `@task.llm_branch(llm_conn_id=..., system_prompt="Classify the support ticket priority.")` — the LLM chooses from "a constrained enum built from the task's downstream IDs" (no string parsing). `HookToolset` exposes only `allowed_methods`. Operator params: `durable`, `require_approval`, `approval_timeout`, `allow_modifications`, `enable_hitl_review`, `max_hitl_iterations`, `hitl_timeout`. Token limits / cost caps ("AIBudget") and a Google ADK backend are listed only under "What's Next" — [Airflow common-ai provider blog](https://airflow.apache.org/blog/common-ai-provider/)
- Migration from `airflow-ai-sdk`: same base decorators, but model config moves to connections; toolsets turn provider hooks into agent tools; extras `[openai] [anthropic] [google] [bedrock] [groq] [mistral]`; no `@task.embed` in the provider — [Astronomer migration post](https://www.astronomer.io/blog/migrating-from-airflow-ai-sdk-to-apache-airflow-s-common-ai-provider/); original SDK on [PyPI airflow-ai-sdk](https://pypi.org/project/airflow-ai-sdk/0.1.3/)
- Durable agents: `AgentOperator(task_id="researcher", prompt=..., llm_conn_id="pydanticai_default", durable=True)` / `@task.agent(llm_conn_id=..., durable=True)`; Airflow 3.3 + provider 0.7.0+ use the task state store, older setups need `[common.ai] durable_cache_path`; cache cleared on success; limitations: no streaming with `durable=True`, model must be concrete, `durable` and `enable_hitl_review` mutually exclusive — [Pydantic AI Airflow durable execution](https://pydantic.dev/docs/ai/integrations/durable_execution/airflow/); `@task.agent` defaults to `durable=False` while classic operators default to `durable=True` on 3.3 — [Astronomer durable execution post](https://www.astronomer.io/blog/durable-execution-with-apache-airflow.md)

**Kestra — `plugin-ai` (LangChain4j)**
- Latest 2.3.2 dated 2026-10-14, min Kestra 2.0.2, "Certified", Kestra Core Team, repo github.com/kestra-io/plugin-ai. Tasks: `completion.ChatCompletion`, `Classification`, `ImageGeneration`, `JSONStructuredExtraction`; `rag.IngestDocument`, `rag.Search`, `rag.ChatCompletion`; `agent.AIAgent` ("autonomous loop with tools, memory, retrievers, guardrails, and optional Langfuse tracing"), `agent.A2AClient`; `mcp.ListTools`, `mcp.CallTool`. 22 providers incl. `Fallback` (chat only; does not fall back on auth/invalid-request errors). Tools: `KestraFlow`, `KestraTask`, nested `AIAgent` (sub-agent), `Skill`, MCP clients (`SseMcpClient`, `StdioMcpClient`, `StreamableHttpMcpClient`, `DockerMcpClient`), `TavilyWebSearch`, `GoogleCustomWebSearch`, `CodeExecution` (Judge0), `A2AClient`. Structured output via `configuration.responseFormat: JSON` + `jsonSchema`/`strictJson` — [Kestra plugin-ai](https://kestra.io/plugins/plugin-ai)
- Example shape (from docs): `type: io.kestra.plugin.ai.agent.AIAgent`, `systemMessage: ...`, `prompt: "Summarize the following content: {{ inputs.text }}"`, `provider: {type: io.kestra.plugin.ai.provider.GoogleGemini, modelName: ..., apiKey: "{{ secret('GEMINI_API_KEY') }}"}`, `tools: [{type: io.kestra.plugin.ai.tool.KestraFlow, namespace: tutorial, flowId: business-automation, description: ...}]`; `KestraFlow` registers tools named `kestra_flow_<namespace>_<flowId>` or a generic tool, with optional `allowedFlows` allow-list; a Policy that injects the provider block is "an Enterprise Edition and Cloud feature" — [Kestra AI agents docs](https://kestra.io/docs/ai-tools/ai-agents); [Kestra KestraFlow tool](https://kestra.io/plugins/io.kestra.plugin.ai.tool.KestraFlow)

**Flyte 2 / Union — `flyte.ai.agents.Agent`**
- Signature: `Agent(name='flyte-agent', instructions='You are a helpful assistant.', model='claude-haiku-4-5', tools=..., mcp_servers=..., skills=..., max_turns=25, call_llm=_default_call_llm, approval_callback=_condition_approval, parallel_tool_calls=True, code_mode=False)`. `tools` may be plain callables, `@flyte.trace` helpers, `@env.task` templates, `LazyEntity` objects, or `AgentTool`; `mcp_servers` load on first use; `skills` are strings/paths appended to the prompt; `call_llm` default uses `litellm.acompletion`; `code_mode=True` makes the LLM write a Python program per turn that runs in the Monty sandbox (`flyte.sandbox.orchestrate_local`, requires `pydantic-monty`); `run(message, memory=None) -> AgentResult` (async via `run.aio`); `add_tool(obj, name=None)`; `AgentProtocol` is what any agent must satisfy for the chat app environment — [Flyte Agent API](https://www.union.ai/docs/v2/flyte/api-reference/flyte-sdk/packages/flyte.ai.agents/agent/); [Flyte AgentProtocol](https://www.union.ai/docs/v2/flyte/api-reference/flyte-sdk/packages/flyte.ai.agents/agentprotocol/)
- Patterns: pure-Python ReAct loop and Plan-and-Execute with parallel fan-out; deploy as on-demand task, scheduled `Trigger`, or `AppEnvironment` webhook; "Flyte assigns a URL, handles TLS, and auto-scales the app"; tutorials include a research loop driving Claude Code in a GPU container that commits results and opens a PR — [Flyte 2 Build an agent](https://www.union.ai/docs/v2/flyte/user-guide/build-agent); [Union deploy agent as service](https://www.union.ai/docs/v2/flyte/user-guide/build-agent/deploy-agent-as-service); [Flyte agent tutorials](https://www.union.ai/docs/v2/flyte/tutorials/agents/)

**Temporal**
- Positioning: "Durable Execution" so agents resume after "a crash, a network timeout, or a multi-day wait for a human to approve a step"; integrations: OpenAI Agents SDK (Python, TS), LangGraph, Deep Agents, Pydantic AI, Vercel AI SDK, Mastra, Google ADK (Python/Go/TS), Google GenAI, Spring AI, Strands Agents — [Temporal AI docs](https://docs.temporal.io/ai)
- OpenAI Agents SDK integration: public preview for the Python SDK announced July 2025 — [Temporal blog](https://temporal.io/blog/announcing-openai-agents-sdk-integration); [BusinessWire 2025-07-30](https://www.businesswire.com/news/home/20250730783559/en/Temporal-and-OpenAI-Launch-Integration-for-Enterprises-Developing-Production-Agents); April 2026: Temporal extension for the SDK's sandbox support, "fork a running agent onto a completely different sandbox provider" — [Temporal sandboxes blog](https://temporal.io/blog/introducing-temporal-and-agentic-sandboxes-openai-agents-sdk)
- Code shape: Agent created inside a Workflow class; tools = Activities via `activity_as_tool(...)`; `Runner.run(agent, prompt)` in `@workflow.run`; "Model calls don't need a user-written Activity. The integration handles LLM invocation" — [Temporal OpenAI Agents SDK cookbook](https://docs.temporal.io/ai/cookbook/openai-agents-sdk-python)

**Prefect / ControlFlow / Marvin**
- ControlFlow README: "the next-generation ControlFlow engine was merged into the Marvin agentic framework, and this repo has been archived" — [ControlFlow (archived) listing](https://awesome.ecosyste.ms/projects/github.com%2Fprefect-archive%2FControlFlow); Marvin 3.x reached 3.2.7 as of March 2026 — [Marvin listing](https://www.everydev.ai/tools/marvin); ControlFlow was built on Prefect 3.0 — [controlflow on PyPI](https://pypi.org/project/controlflow/0.8.1)
- Prefect itself positions "Pause flows for human intervention or approval" and "Interactive workflows" — [Prefect intro](https://docs.prefect.io/v3/develop/pause-resume); no `prefect-ai` package found.

**Dagster**
- No "agent as task" primitive found. Official material: a prompt-engineering tutorial (pipeline generating prompts for Anthropic and validating outputs across assets) — [Dagster prompt-engineering tutorial](https://docs.dagster.io/tutorials/prompt-engineering); 2026-01-27 webinar on LLM-assisted *development* (Claude Code/Cursor, sub-agents, skills, MCP) — [Dagster event](https://dagster.io/events/best-practices-for-llm-dagster-development); Compass/Dagster+ AI (operational agents) — see KQ7.

### Inferences
- Common denominators of the native primitives: (1) declarative model/provider selection decoupled from code (Airflow connections, Kestra `provider` block, Flyte `model` string + `call_llm` hook); (2) tools that are themselves orchestrator tasks/flows (Airflow `HookToolset`/`SQLToolset`, Kestra `KestraFlow`/`KestraTask`, Flyte `@env.task` as tool, Temporal `activity_as_tool`); (3) MCP as a tool source (Kestra, Flyte); (4) typed/structured output (Airflow `output_type`, Kestra `JSON` response format); (5) a bounded loop (`max_turns=25` in Flyte; Airflow `max_hitl_iterations`); (6) per-tool approval (Flyte `requires_approval`, Airflow `require_approval`).
- "LLM-driven branching constrained to downstream task IDs" (Airflow `@task.llm_branch`, Kestra agent choosing a `KestraFlow`) is the idiom that keeps the static graph intact while letting the model pick the path.

### Gaps
- Exact 0.7.x changelog of common-ai (features beyond durable cache) was not fetched.
- Kestra guardrails configuration and `maxSequentialToolsInvocations` not found on fetched pages.
- Flyte `Agent` code examples (verbatim) are not on the API page; the tutorial pages were not fetched.

---

## KQ9. Known pain points / criticisms for agent workflows (long-running tasks, non-determinism, cost, token budgets)

### Takeaway
The recurring critiques are: static pre-declared graphs vs. dynamic agent loops; no durability *inside* a task (a crash at minute 40 restarts at minute 0; retries re-pay completed LLM calls); hand-rolled state workarounds; and absent cost/token controls. Airflow's own vendor (Astronomer) concedes the first two points for pre-3.3 Airflow and claims 3.3 closes them via the task state store and `durable=True`; token budgets remain unshipped in Airflow (listed as future work). Most critiques come from vendors of durable-execution engines, so they are partisan.

### Cited Findings
- Vendor critique: standard engines are "engineered for fixed DAGs, scheduled data pipelines, or CI/CD tasks, not long-running, stateful runs with probabilistic steps like LLM interactions" — [Diagrid FAQ](https://www.diagrid.io/faq/workflow-engine-alternatives/why-arent-standard-workflow-engines-ideal-for-agentic-ai-workloads); Airflow "DAG is defined ahead of time, with the scheduler deciding when each task runs" — [Diagrid Catalyst vs Airflow](https://www.diagrid.io/faq/workflow-engine-alternatives/how-does-catalyst-compare-to-apache-airflow-for-ai-agent-workflows)
- Restate: durable execution "doesn't require an up-front defined workflow graph"; wrap "expensive and non-deterministic actions, such as LLM inference calls and tool invocations, into durable steps" — [Restate blog](https://restate.dev/blog/agentic-workflows-are-just-code-treat-them-that-way)
- Astronomer (Airflow vendor) acknowledging pre-3.3 limits: Airflow "could not survive a crash inside a long running task, so a job that died at minute forty of a fifty minute run started again at minute zero"; an agent making 19 model/tool calls whose 20th times out "pays for all nineteen again"; teams hand-build state ("a table of offsets, a Variable holding a cursor, or one task split into a 'submit' task and a 'wait' task"); claim that with 3.3 "Airflow's durability boundary moves down a level, from between tasks to inside one"; comparison: Temporal replays an event log and requires deterministic workflow code, Airflow uses explicit checkpoints; "both approaches are at-least-once, so side effects still need idempotency"; "Airflow tasks are not deterministic by default, so replay would require rewriting how tasks are written" — [Astronomer durable execution post](https://www.astronomer.io/blog/durable-execution-with-apache-airflow.md)
- Pydantic AI on Airflow durable mode: requests that can't be fingerprinted "fall back to positional replay, so runs should be deterministic across retries"; no streaming; `durable` and HITL review mutually exclusive — [Pydantic AI Airflow durable execution](https://pydantic.dev/docs/ai/integrations/durable_execution/airflow/)
- Cost/token control: Airflow common-ai logs token usage but "token limits and cost caps (AIBudget) are listed only under 'What's Next'" — [Airflow common-ai provider blog](https://airflow.apache.org/blog/common-ai-provider/); Kestra exposes `tokenUsage` output and provider/tool call counters but no budget primitive was found — [Kestra AI agents docs](https://kestra.io/docs/ai-tools/ai-agents); Flyte bounds loops by `max_turns=25` — [Flyte Agent API](https://www.union.ai/docs/v2/flyte/api-reference/flyte-sdk/packages/flyte.ai.agents/agent/)
- Prefect timeouts: on the default `ThreadPoolTaskRunner`, a task timeout "cannot interrupt blocking operations" — a practical issue for hung LLM/tool calls — [Prefect write-tasks](https://docs.prefect.io/v3/develop/write-tasks.md)
- Airflow HITL pre-3.3 occupied the Triggerer; 3.3's `awaiting_input` fixes that but shipped with CVE-2026-67260 (scheduler-side deserialization) fixed in 3.3.1 — [Astronomer HITL guide](https://www.astronomer.io/docs/learn/airflow-3/airflow-human-in-the-loop); [CVE-2026-67260](https://app.opencve.io/cve/CVE-2026-67260)
- Dagster executors: pluggable but "not currently well-documented", internal APIs changing — [Dagster run executors](https://dagster.io/docs/guides/operate/run-executors)
- Flyte 2 OSS: "distributed execution coming to open source soon" (local only in OSS at time of docs); API docs show different SDK versions, "changing quickly" — [Union docs](https://www.union.ai/docs/)
- Diagrid caveat: durable execution "does not equate to exactly-once delivery" — [Diagrid FAQ](https://www.diagrid.io/faq/workflow-engine-alternatives/when-should-i-use-catalyst-instead-of-apache-airflow-for-ai-agent-workflows)

### Inferences
- The gap every classic orchestrator is now patching is "checkpoint inside the step" (Airflow 3.3 state store / durable cache; Flyte `@flyte.trace`). A new framework should treat per-step LLM/tool-call memoization as a core primitive rather than an add-on.
- No system in this set ships a token/cost budget as a scheduling constraint; this is open design space.
- Determinism requirements differ: Temporal requires deterministic workflow code; Airflow's cache-replay only requires deterministic *request ordering* within a task; Flyte's traced spans checkpoint explicitly.

### Gaps
- Independent (non-vendor) benchmarks or user post-mortems on running agents on Airflow/Prefect/Dagster were not found.
- Temporal Event History size limits / Continue-As-New guidance for long agent loops were not on fetched pages.

---

## KQ10. Landscape/ownership changes relevant to positioning (context for the report)

### Takeaway
As of October 2026 the vendor map has shifted: Prefect agreed to acquire Dagster Labs (announced 2026-07-13), ControlFlow was folded into Marvin, Union is marketing Flyte 2 primarily as an "AI workflows/agents" platform, and Astronomer/Airflow brand 3.3 as "a durable execution platform built for both classic data engineering and modern AI workloads".

### Cited Findings
- Prefect "agreed to acquire Dagster Labs", announced 2026-07-13; Dagster/Dagster+ keep names, OSS license and roadmap; pricing unchanged; Nick Schrock and Pete Hunt as strategic advisors; combined pitch "from pipelines to agentic workflows"; Prefect also maintains FastMCP (92M+ downloads claimed). Completion status not stated — [Dagster blog: Prefect is acquiring Dagster](https://dagster.io/blog/prefect-is-acquiring-dagster); [Pulse 2.0](https://pulse2.com/prefect-acquiring-dagster-labs/). A conflicting third-party claim that Schrock "is stepping back" — [techinterview.org](https://www.techinterview.org/companies/dagster-interview-guide/?format=md) — contradicts the company statement.
- Astronomer: "with Airflow 3.3, it is now a durable execution platform built for both classic data engineering and modern AI workloads" — [Astronomer durable execution post](https://www.astronomer.io/blog/durable-execution-with-apache-airflow.md)
- Union: "Flyte 2 ... build AI workflows in pure Python with built-in durability, reproducibility, and recovery" — [Union docs](https://www.union.ai/docs/)
- InfoQ coverage of Temporal's AI-agent push (Sept 2025) — [InfoQ](https://www.infoq.com/news/2025/09/temporal-aiagent)

### Inferences
- Prefect+Dagster consolidation means two of the six "classic" codebases now share an owner; design idioms from both (Prefect's imperative flows, Dagster's asset graph) may converge, but as of Oct 2026 the docs are unchanged.

### Gaps
- Whether the Prefect–Dagster deal has closed, and any roadmap statements about merging agent features, were not found.
