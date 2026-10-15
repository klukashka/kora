# Pluggable execution runtimes and sandboxes for coding-agent pipeline steps (as of 2026-10-14)

Scope: (a) container/Kubernetes workflow engines and their runtime abstraction, (b) agent sandbox providers, (c) "runtime protocol" design for third-party backends, (d) long-running steps / heartbeats / preemption / cost. Versions and dates noted where sources state them. All claims are sourced inline; unsourced material is in Gaps.

---

## KQ1. What is the minimal interface a custom runtime backend must implement in Airflow, Prefect, Dagster, Argo, Nomad (and adjacent systems)?

### Takeaway
Every mature system converges on the same small verb set — submit/launch, poll/sync status, cancel/terminate, plus optional health-check, recover/resume, logs and cleanup — but the *shape* differs: Python ABCs (Airflow `BaseExecutor`, Prefect `BaseWorker`, Dagster `RunLauncher`), gRPC/go-plugin (Nomad task drivers), HTTP sidecar RPC (Argo executor plugins), or a CRD that an external controller reconciles (Tekton CustomRun, Kubernetes Job `managedBy`). The cheapest extension points for a new agent runtime are Argo's `template.execute` HTTP contract (one endpoint + requeue), Prefect's `run()`/`kill_infrastructure()` pair, and Dagster's `launch_run()`/`terminate()` pair.

### Cited Findings

**Apache Airflow (docs version 3.3.2)**
- Built-in executors: `LocalExecutor` (default), `CeleryExecutor`, `KubernetesExecutor`, `EdgeExecutor` (Edge3 provider), `BatchExecutor`/`EcsExecutor` (Amazon provider); hybrid `LocalKubernetesExecutor`/`CeleryKubernetesExecutor` removed in 3.0.0; multiple executors supported since 2.10.0 (`core.executor` comma list, per-task `executor=` param). — [Airflow executor docs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/executor/index.html)
- Custom executor = subclass `BaseExecutor`, set `core.executor` to module path. **Mandatory methods: `sync`, `execute_async`, `_process_workloads`.** Optional: `start`, `end`, `terminate`, `try_adopt_task_instances`, `get_cli_commands`, `get_task_log`. "Important but not required to override": `heartbeat`, `queue_workload`, `get_event_buffer`, `has_task`, `send_callback`. Compatibility attributes: `supports_pickling`, `sentry_integration`, `is_local`, `is_single_threaded`, `is_production`, `serve_logs`. — [Airflow executor docs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/executor/index.html)
- Task-level liveness: "zombie tasks" are now "Task Instance Heartbeat Timeouts"; config `AIRFLOW__SCHEDULER__TASK_INSTANCE_HEARTBEAT_SEC`, `..._TIMEOUT`, `..._TIMEOUT_DETECTION_INTERVAL`; detected stuck tasks are failed or retried. `execution_timeout` (timedelta) raises `AirflowTaskTimeout`. — [Airflow tasks docs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html)

**Prefect 3 (BaseWorker)**
- Required class attributes: `type`, `job_configuration` (Pydantic `BaseJobConfiguration` subclass); optional `job_configuration_variables`, `_documentation_url`, `_logo_url`, `_description`. Required method: `async run(flow_run, configuration, task_status) -> BaseWorkerResult` — create infra, call `task_status.started(<infra id>)` (the id is later used for teardown on cancellation), monitor, return result with `status_code`/`identifier`. `BaseJobConfiguration` base fields: `name`, `env`, `labels`, `command`; override `prepare_for_flow_run(...)` to customize. Workers register via `prefect worker start --type` or a `prefect.collections` entry point. — [Prefect: developing a custom worker](https://docs.prefect.io/v3/advanced/developing-a-custom-worker)
- API reference method set: `run(flow_run, configuration, task_status=None)`, `kill_infrastructure(infrastructure_pid, configuration, grace_seconds=30)`, `setup()`, `teardown(*exc_info)`, `start(run_once=False, with_healthcheck=False)`, `get_and_submit_flow_runs()`, `sync_with_backend()` (refreshes pool/queue info *and sends the worker heartbeat*), `is_worker_still_polling(query_interval_seconds)` (health window = interval × 30), `get_status()`, `get_default_base_job_template()`, cleanup-handler registry hooks; `BaseJobConfiguration.from_template_and_values`, `json_template`, `prepare_for_flow_run`, `resolve_for_flow_run`. — [prefect.workers.base reference](https://docs.prefect.io/v3/api-ref/python/prefect-workers-base)
- Polling defaults: workers poll every 15 s (`PREFECT_WORKER_QUERY_SECONDS`), prefetch 10 s before scheduled start (`PREFECT_WORKER_PREFETCH_SECONDS`); worker is `OFFLINE` after missing three heartbeats, "at most 90 seconds" (`PREFECT_WORKER_HEARTBEAT_SECONDS`). Worker types: `process`, `kubernetes`, `docker`, `ecs`, `cloud-run-v2`, `vertex-ai`, `azure-container-instance`, `coiled`. — [Prefect workers concept](https://docs.prefect.io/v3/concepts/workers)

**Dagster (RunLauncher)**
- Source (`dagster/_core/launcher/base.py`): abstract `launch_run(context: LaunchRunContext) -> None` and `terminate(run_id: str) -> bool`; non-abstract `dispose()`, `join(timeout=30)`, `supports_check_run_worker_health` (default False), `check_run_worker_health(run) -> CheckRunHealthResult` (default NotImplementedError), `get_run_worker_debug_info(run, include_container_logs=True) -> str|None`, `supports_resume_run` (default False), `resume_run(context: ResumeRunContext)`. `LaunchRunContext(dagster_run, workspace)`; `ResumeRunContext(dagster_run, workspace, resume_attempt_number)`. `WorkerStatus` enum: `RUNNING, NOT_FOUND, FAILED, SUCCESS, UNKNOWN`; `CheckRunHealthResult(status, msg, transient, run_worker_id)`. — [dagster launcher/base.py](https://github.com/dagster-io/dagster/blob/master/python_modules/dagster/dagster/_core/launcher/base.py)
- Concept: the launcher "receives the ID of a created run" and allocates a *run worker* (process/container/K8s Job); the in-worker `Executor` (abstract `execute(plan_context, execution_plan)` + `retries` property) then fans out steps. Built-ins: `DefaultRunLauncher` (process per run next to the code location), `K8sRunLauncher` (K8s Job per run), `EcsRunLauncher`, `DockerRunLauncher`, `CeleryK8sRunLauncher`. `QueuedRunCoordinator` enqueues runs for the daemon to launch. — [Dagster run launchers](https://dagster.io/docs/deployment/execution/run-launchers); [Dagster internals API](https://dagster.io/docs/api/dagster/internals)

**Argo Workflows (executor plugins, HTTP sidecar)**
- Plugin = sidecar HTTP server in the Agent pod. One RPC: `POST /api/v1/template.execute`; request carries `workflow.metadata.name` and `template{name, inputs, outputs, plugin}`; response `{node: {phase: Succeeded|Failed|Pending|Running, message, outputs.parameters}, requeue: "2m"}`. Returning `{}` = "cannot execute this template". `requeue` with Pending/Running phase makes the controller call again after the duration (this is the poll loop). Auth: `Authorization: Bearer <token>` matching `/var/run/argo/token`, else 403. Error semantics: connection errors/timeouts/503 transient (retried); 404 = method unsupported (not called again this workflow); other 4xx/5xx fatal. Registration: `ExecutorPlugin` manifest → `argo executor-plugin build .` → ConfigMap labelled `workflows.argoproj.io/configmap-type=ExecutorPlugin`. Gated by `ARGO_EXECUTOR_PLUGINS=true` / `ARGO_WORKFLOW_LEVEL_EXECUTOR_PLUGINS=true`. Resource limits and `securityContext` mandatory. — [Argo executor plugins](https://argo-workflows.readthedocs.io/en/latest/executor_plugins/)
- Artifact *driver* plugins (sidecar, registered under `artifactDrivers`) exist from v4.0+. — [Argo artifact repository config](https://argo-workflows.readthedocs.io/en/latest/configure-artifact-repository/)
- Latest releases: v4.1.5 and v4.0.13 (09 Oct 2026). — [Argo releases](https://github.com/argoproj/argo-workflows/releases)

**HashiCorp Nomad task driver plugins (docs for Nomad v2.0.x)**
- Base plugin: `PluginInfo()`, `SetConfig(config)`, `ConfigSchema()`. Driver API: `TaskConfigSchema()`, `Capabilities()` (signals, exec, fs/network isolation modes, `MustInitiateNetwork`, mounts, log collection), `Fingerprint(ctx)` (streams Undetected/Unhealthy/Healthy), `StartTask(TaskConfig) -> TaskHandle, DriverNetwork`, `RecoverTask(TaskHandle)` (reattach after driver restart; error ⇒ task marked `lost`), `WaitTask(ctx,id) -> chan ExitResult`, `StopTask(id, timeout, signal)`, `DestroyTask(id, force)`, `InspectTask(id)`, `TaskStats(ctx,id,interval)`, `TaskEvents(ctx)`, optional `SignalTask`, `ExecTask`, `Init`, `Shutdown` (~2 s + 2 s grace), `CreateNetwork/DestroyNetwork` when `MustInitiateNetwork`. Tasks outlive the driver; "drivers should keep as little state as possible". Skeleton repo `nomad-skeleton-driver-plugin`; `exec2` is the reference external driver. — [Nomad task driver plugins](https://developer.hashicorp.com/nomad/docs/concepts/plugins/task-drivers)

**Tekton Custom Tasks (CRD extension point)**
- A `taskRef` with `apiVersion`+`kind` creates a `CustomRun` (v1beta1 from v0.47.0); "a custom controller on the cluster must watch and update these resources". Params, matrix, workspaces, results, timeout and retries all work with custom tasks. — [Tekton Pipelines docs](https://tekton.dev/docs/pipelines/pipelines/)

**Kubernetes Job delegation**
- `spec.managedBy` (stable v1.35, first in v1.30; immutable) lets an external controller own a Job; the controller must follow Job API/status semantics and must not use the `batch.kubernetes.io/job-tracking` finalizer. — [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/)

**Buildkite agent (agent-side protocol)**
- The agent polls the Buildkite Agent API over HTTPS ("no need to forward ports"), registers into the org's pool, accepts a job, runs it, streams output and posts exit status; `buildkite-agent meta-data` and `buildkite-agent artifact` share state across machines. Agents are ordered by most-recent successful job to favour warm caches. Hosted agents are always ephemeral. — [Buildkite agent overview](https://buildkite.com/docs/agent/v3)
- Job lifecycle hooks in order: `pre-bootstrap` (agent only; gates whether the job may run, exit 0 permits; gets `BUILDKITE_ENV_FILE`) → `environment` → `pre-checkout` → `checkout` → `post-checkout` → `pre-command` → `command` → `post-command` → `pre-artifact` → `post-artifact` → `pre-exit`; agent hooks `agent-startup` (v3.42.0+) / `agent-shutdown`. Env changes are captured by a scriptwrapper and forwarded between phases. On the Agent Stack for Kubernetes, hooks run in separate containers (checkout vs command). — [Buildkite hooks](https://buildkite.com/docs/agent/v3/hooks)

**CNCF Serverless Workflow / "Open Workflow" DSL**
- DSL has 12 task types: Call, Do, Emit, For, Fork, Listen, Raise, Run (container/script/shell/workflow), Set, Switch, Try, Wait; runtimes "must implement them" and should pass the CTK conformance scenarios. Statuses include `waiting` (events/time) and `suspended` (manual pause). Timeouts must raise `.../errors/timeout` (status 408). Retry policy lives in `try/catch` (example: 503 → 3 s delay, linear backoff, limit 5). Examples use `dsl: '1.0.3'`. — [Open Workflow dsl.md](https://github.com/serverlessworkflow/specification/blob/main/dsl.md)
- Runtimes listed: Apache KIE SonataFlow, Java SDK reference implementation, Quarkus Flow, Lemline, Synapse; SDKs: .NET, Go, Java, PHP, Python, Rust, TypeScript; CNCF Sandbox since 2020-07-14. — [specification repo README](https://github.com/serverlessworkflow/specification)
- CNCF project page now titled "Open Workflow Specification"; repo README still describes "Serverless Workflow DSL"; a GitHub issue proposes "CNCF Workflow Specification" — naming is in flux. — [CNCF project page](https://www.cncf.io/projects/open-workflow-specification/); [issue #1127](https://github.com/serverlessworkflow/specification/issues/1127)

### Inferences
- Cross-system minimal verb set (union of what each requires): **submit/launch** (Airflow `execute_async`, Prefect `run`, Dagster `launch_run`, Nomad `StartTask`, Argo `template.execute`), **poll/sync** (Airflow `sync`, Prefect in-`run` monitoring, Dagster `check_run_worker_health`, Nomad `WaitTask`/`InspectTask`, Argo `requeue`), **cancel** (Airflow `terminate`, Prefect `kill_infrastructure`, Dagster `terminate`, Nomad `StopTask`/`DestroyTask`), **recover/adopt after controller restart** (Airflow `try_adopt_task_instances`, Dagster `resume_run`, Nomad `RecoverTask`), **logs** (Airflow `get_task_log`, Dagster `get_run_worker_debug_info`), **heartbeat** (Airflow executor `heartbeat` + task-instance heartbeat; Prefect `sync_with_backend`; Nomad `Fingerprint` stream), **env/secret injection** (Prefect `BaseJobConfiguration.env`; Argo plugin `secretKeyRef`; Buildkite `environment` hook). Artifact upload/download is *not* part of any runtime interface above — it is handled by the workflow layer (Argo artifact repo, Tekton workspaces, Buildkite `artifact` CLI, GitHub `upload-artifact`).
- Two topologies for the poll loop: push-based with controller-driven re-invocation (Argo `requeue`, Kubernetes reconcile) vs. worker-driven long poll (Buildkite agent, Prefect workers, Airflow executors). Agent-session runtimes that run for hours fit the Argo/Kubernetes "requeue + external status" model better than the "hold a worker slot" model.
- The Argo plugin contract (one HTTP method, three phases, requeue, bearer token) is the smallest complete "runtime protocol" found; a Kora-style agent runtime could be exposed exactly this way to Argo.

### Gaps
- Airflow `BaseExecutor` page does not list `queue_command`/`trigger_tasks`/`cleanup_stuck_queued_tasks`; the page uses `queue_workload`. Exact signatures of `_process_workloads` not shown.
- Prefect API-ref page does not document `heartbeat_interval_seconds`, `_propose_pending_state`, `_submit_run`, or `BaseWorkerResult` fields; the 30 s heartbeat figure is only an inference (90 s / 3 missed).
- Dagster source did not show a `supports_run_worker_crash_recovery` member.
- The Argo plugin doc page did not state the version in which executor plugins were introduced.
- I found no public document of the Buildkite Agent API wire protocol (endpoints, long-poll interval) — only the behavioural description.

---

## KQ2. How do Argo Workflows and GitHub Actions (and GitLab, Buildkite, Tekton, Airflow) implement approval gates and artifact passing between steps?

### Takeaway
Argo models approval as a `suspend: {}` template resumed by `argo resume` (optionally by node selector) and passes artifacts via an artifact repository with `outputs.artifacts` → `arguments.artifacts.from`. GitHub Actions models approval as *environment protection rules* (up to 6 required reviewers, wait timer, branch rules, custom GitHub-App rules) attached to a job's `environment:`, and passes data via immutable `actions/upload-artifact` blobs or ≤1 MB job `outputs`. GitLab (protected-environment approvals + `when: manual`), Buildkite (`block` steps + meta-data), Tekton (`ApprovalTask` custom task, vendor-provided) and Airflow 3.1+ (`ApprovalOperator`, `awaiting_input` state in 3.3) all follow one of these two patterns.

### Cited Findings

**Argo Workflows — suspend/resume**
- Suspend template YAML (verbatim from docs): — [Argo suspending](https://argo-workflows.readthedocs.io/en/latest/walk-through/suspending/)
  ```yaml
  apiVersion: argoproj.io/v1alpha1
  kind: Workflow
  metadata:
    generateName: suspend-template-
  spec:
    entrypoint: suspend
    templates:
    - name: suspend
      steps:
      - - name: build
          template: hello-world
      - - name: approve
          template: approve
      - - name: delay
          template: delay
      - - name: release
          template: hello-world
    - name: approve
      suspend: {}
    - name: delay
      suspend:
        duration: "20"    # Must be a string. Default unit is seconds. Could also be a Duration, e.g.: "2m", "6h"
    - name: hello-world
      container:
        image: busybox
        command: [echo]
        args: ["hello world"]
  ```
- "Once suspended, a Workflow will not schedule any new steps until it is resumed." No-duration suspend waits for `argo resume WORKFLOW`; with `duration` it auto-resumes. The page does not cover who may approve or audit of approvals. — [Argo suspending](https://argo-workflows.readthedocs.io/en/latest/walk-through/suspending/)
- `argo resume WORKFLOW1 WORKFLOW2... [flags]`, `argo resume @latest`, `argo resume --node-field-selector inputs.parameters.myparam.value=abc` (resume a specific suspended node). — [argo resume CLI](https://argo-workflows.readthedocs.io/en/latest/cli/argo_resume/)

**Argo Workflows — artifacts**
- Producer: `outputs.artifacts: [{name: hello-art, path: /tmp/hello_world.txt}]`; consumer: `inputs.artifacts: [{name: message, path: /tmp/message}]`; binding: `arguments.artifacts: [{name: message, from: "{{steps.generate-artifact.outputs.artifacts.hello-art}}"}]` (DAG uses `{{tasks.X.outputs.artifacts.Y}}`). Default packaging is gzipped tar; `archive: {none: {}}` or `archive: {tar: {compressionLevel: 0}}`; `mode: 0755` sets permissions. Artifact GC strategies `OnWorkflowCompletion` / `OnWorkflowDeletion` / `Never` per artifact; GC pods `<wfName>-artgc-*`; finalizer `workflows.argoproj.io/artifact-gc`; `forceFinalizerRemoval` (v3.5+); large artifacts may need `podSpecPatch` to raise init-container memory. Security: Argo explicitly allows `../` in keys. — [Argo artifacts](https://argo-workflows.readthedocs.io/en/latest/walk-through/artifacts/)
- Repository backends: Artifactory, Azure Blob, GCS, Git, HDFS, HTTP, OSS, Plugin (v4.0+), Raw, S3 (incl. MinIO). Selection priority: workflow `artifactRepositoryRef` → namespace ConfigMap `artifact-repositories` with `workflows.argoproj.io/default-artifact-repository` annotation (v3.0+) → `artifactRepository` key in workflow-controller ConfigMap. Config: `endpoint`, `bucket`, `keyFormat` (workflow variables), `accessKeySecret`/`secretKeySecret`, `useSDKCreds: true` for IRSA/managed identity/RRSA; assume-role and S3 Access Grants v3.6+; artifact streaming S3/Azure v3.4+, HTTP/Artifactory v3.5+, OSS v3.6+, Plugin v4.0+. — [Argo artifact repository](https://argo-workflows.readthedocs.io/en/latest/configure-artifact-repository/)
- DAG example (diamond A→{B,C}→D with `dependencies:`); DAGs are fail-fast by default, `failFast: false` lets branches finish; steps and DAG templates can call each other. — [Argo DAG](https://argo-workflows.readthedocs.io/en/latest/walk-through/dag/)
- `retryStrategy`: `limit` (string), `retryPolicy` ∈ `Always | OnFailure (default) | OnError | OnTransientError (3.0+, matches TRANSIENT_ERROR_PATTERN)`; `expression` (v3.2+) over `lastRetry.exitCode/status/duration/message` ANDed with retryPolicy (default policy becomes `Always` when expression set in 3.5+). — [Argo retries](https://argo-workflows.readthedocs.io/en/latest/retries/)
- Backoff example (verbatim fields): `backoff: {duration: "1", factor: "2", maxDuration: "1m", cap: "5"}` — all strings, default unit seconds. — [retry-backoff.yaml](https://raw.githubusercontent.com/argoproj/argo-workflows/main/examples/retry-backoff.yaml)
- WorkflowSpec fields of interest: `activeDeadlineSeconds`, `hooks`, `parallelism`, `podGC`, `podSpecPatch`, `retryStrategy` (applies to all templates), `shutdown`, `synchronization` (locks), `templateDefaults`, `ttlStrategy`, `workflowTemplateRef`, `suspend`. — [Argo fields reference](https://argo-workflows.readthedocs.io/en/latest/fields/)

**GitHub Actions — environments & approvals**
- Protection rules: *Required reviewers* up to 6 people/teams, only one must approve; *Prevent self-review*; *Wait timer* (minutes); *Deployment branches and tags* (patterns set separately for branches/tags); *Allow administrators to bypass* (can be disabled); *custom deployment protection rules* via GitHub Apps. Environment secrets/variables are released to a job only after rules pass. Free plan: environments only for public repos; private repos need Pro/Team. — [GitHub manage environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- Job syntax: `environment: staging_environment` or
  ```yaml
  environment:
    name: production_environment
    url: https://github.com
  ```
  "Deployment protection rules must pass before a job that references the environment is sent to a runner." Job `outputs` are ≤1 MB per job, read as `${{ needs.job1.outputs.output1 }}`. `runs-on` accepts label arrays (`[self-hosted, linux]`) or `{ group: build-runners }`. — [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- `timeout-minutes` default 360; matrix max 256 jobs per run; `GITHUB_TOKEN` expires at job end or after 24 h max; reusable workflows via `uses: {owner}/{repo}/.github/workflows/{file}@{ref}` with `with:` inputs and `secrets: inherit`; `container:` (image, credentials, env, ports, volumes, options — `--network`/`--entrypoint` unsupported) and `services:`. — [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)

**GitHub Actions — artifacts**
- `actions/upload-artifact@v7` inputs: `name` (default `artifact`), `path`, `if-no-files-found` (warn|error|ignore), `retention-days` (1–90, default 90, repo setting may lower), `compression-level` (0–9, default 6), `overwrite`, `include-hidden-files`, `archive`; outputs `artifact-id`, `artifact-url`, `artifact-digest`. Limits: up to 500 artifacts per job; names unique; v4+ artifacts immutable (overwrite creates a new ID); no multi-job upload to one artifact; zipped uploads lose permissions (use tar + `archive: false`); v4+ not supported on GHES. — [actions/upload-artifact](https://github.com/actions/upload-artifact)
- Storage billing: shared artifacts/Packages $0.25/GB/month; Actions cache $0.07/GB/month. — [GitHub Actions billing](https://docs.github.com/en/billing/managing-billing-for-your-products/about-billing-for-github-actions)

**GitLab CI**
- Deployment approvals (Premium/Ultimate): per protected environment, "Deployments are blocked until all required approvals are given"; approvers = users/groups/admins; self-approval blocked unless "Allow pipeline triggerer to approve deployment"; required approvals must be fewer than users allowed to deploy; deployment job does **not** need `when: manual`, but after approval someone must run the job manually; blocked deployments show a **blocked** label / API `status`. Example: — [GitLab deployment approvals](https://docs.gitlab.com/ci/environments/deployment_approvals/)
  ```yaml
  production:
    stage: deploy
    script:
      - echo "Deploying to $CI_ENVIRONMENT_NAME"
    environment:
      name: $CI_JOB_NAME
      action: start
  ```
- `when:` values `on_success | on_failure | always | manual | delayed (start_in ≤ 1 week) | never`; `allow_failure` defaults true for manual jobs (false under `rules:when: manual`), `allow_failure: false` on a manual job makes it blocking; `artifacts` (`paths`, `expire_in`, `reports` always uploaded, `when`), `needs` max 50 (`needs:artifacts` default true), `parallel` 1–200 / `parallel:matrix` ≤200, `retry:max` 0–2 with `retry:when` (`script_failure`, `api_failure`, `runner_system_failure`, `runner_configuration_error`, `stale_schedule`, `server_timeout_running`, `data_integrity_failure`) and `retry:exit_codes` (GA 17.5), `timeout` "can't be longer than the runner's timeout", `resource_group` (semaphore across pipelines; default process mode `unordered`), `interruptible` default false, `trigger:include` child pipelines with `strategy: mirror`. — [GitLab CI YAML reference](https://docs.gitlab.com/ci/yaml/)

**Buildkite**
- Block step: `steps: [- block: "Are we ready?"]`, with `prompt`, `fields` (text/select), `key`, `depends_on`, `branches`, `blocked_state` (passed|failed|running), `allowed_teams`; unblock needs write access to the pipeline; field values go to build meta-data (`buildkite-agent meta-data get`); block vs input step differ only in implicit dependencies. — [Buildkite block step](https://buildkite.com/docs/pipelines/configure/step-types/block-step)

**Tekton**
- No built-in approval; the docs' guard examples use a user-defined `manual-approval` Task; a "ManualApprovalGate" operator page is listed separately. Results pass via `$(tasks.<name>.results.<name>)`; workspaces map by name; `finally` tasks; `retries`; per-task `timeout` (Go duration) bounded by PipelineRun timeout. — [Tekton Pipelines](https://tekton.dev/docs/pipelines/pipelines/)
- Results travel in the container termination message (4096 bytes shared by all steps in the pod, ~<1 KB/step for 10 steps); `results-from: sidecar-logs` raises to 4 KB per result by default, `max-result-size` configurable but bounded by etcd/CRD size. Native Kubernetes sidecars via `enable-kubernetes-sidecar` (v0.62+). Docs track release-v1.15.x. — [Tekton Tasks](https://tekton.dev/docs/pipelines/tasks/)
- `openshift-pipelines/manual-approval-gate`: CRD controller; `taskRef: {apiVersion: openshift-pipelines.org/v1alpha1, kind: ApprovalTask}` with `approvers` (users, `group:` prefix), `numberOfApprovalsRequired`, `description`, timeout (on expiry task is rejected and the PipelineRun fails); `tkn-approvaltask` CLI. Red Hat ships it as Technology Preview in OpenShift Pipelines. — [manual-approval-gate repo](https://github.com/openshift-pipelines/manual-approval-gate); [Red Hat docs](https://docs.redhat.com/en/documentation/red_hat_openshift_pipelines/1.18/html/creating_cicd_pipelines/using-manual-approval)

**Airflow HITL**
- Operators (added 3.1): `HITLOperator`, `HITLEntryOperator`, `ApprovalOperator` (approve/reject), `HITLBranchOperator`; params `subject`, `body`, `options`, `multiple`, `params`, `defaults` (applied on timeout), `response_timeout`, `assigned_users`, `notifiers`. In 3.3+ the task sits in scheduler-managed `awaiting_input` holding no worker/triggerer/pool slot; in 3.1/3.2 it was a deferrable trigger. Respond via UI "Required Actions" or `PATCH .../taskInstances/{task_id}/{map_index}/hitlDetails`. Example: — [Airflow HITL tutorial 3.3.2](https://airflow.apache.org/docs/apache-airflow/3.3.2/tutorial/hitl.html)
  ```python
  valid_input_and_options = ApprovalOperator(
      task_id="valid_input_and_options",
      subject="Are the following input and options valid?",
      defaults="Reject",
      response_timeout=datetime.timedelta(minutes=5),
      assigned_users=[{"id": "1", "name": "airflow"}, {"id": "admin", "name": "admin"}],
  )
  ```

### Inferences
- Two approval idioms: (1) *gate as a node* (Argo `suspend`, Buildkite `block`, Tekton `ApprovalTask`, Airflow `ApprovalOperator`) — the gate is a first-class step with its own state and can carry inputs (Buildkite fields, Airflow params); (2) *gate as a policy on a target* (GitHub environments, GitLab protected environments) — approvals attach to a deployment target and also gate secret release. For agent pipelines, idiom (1) is needed when the approval must feed data (e.g., a reviewer's comment) back into the next step; idiom (2) is better for release-type gates.
- Only Argo's suspend has no built-in notion of *who* approved (RBAC on the resume verb is the only control); GitHub, GitLab, Buildkite and the Tekton gate all have explicit approver lists.
- Artifact passing is uniformly "object store + declarative reference", never streaming; large agent workspaces (repo checkouts) should be passed as a single tarball/snapshot reference rather than many small artifacts, given GitHub's 500-per-job and Tekton's termination-message limits.

### Gaps
- GitHub docs page did not state the wait-timer maximum (commonly cited as 43,200 min) — unverified here.
- GitLab docs did not state a maximum number of approval rules or the approval REST endpoint path; GitLab `timeout` maximum not stated.
- Argo REST endpoint for resume not shown in the fetched pages (only CLI).
- Upstream (non-vendor) Tekton approval project release status not found.

---

## KQ3. How do container/Kubernetes workflow engines model a pluggable runtime, and how do they handle long-running (hours) steps, heartbeats, preemption/resume and cost?

### Takeaway
Kubernetes-native engines (Argo, Tekton, Jobs, Agent Sandbox) push "runtime" down to the Pod/RuntimeClass and treat liveness as the kubelet's problem; hosted CI (GitHub, GitLab, Buildkite) treats runtime as a *runner/agent* that polls, with hard job time ceilings (GitHub 360 min default). Preemption handling is most explicit in Kubernetes Jobs (`podFailurePolicy` + `DisruptionTarget`, `suspend`, `backoffLimitPerIndex`) and in Airflow 3.3's `durable` KPO. Cost attribution is native only in hosted CI (per-minute runner rates); K8s engines leave it to labels/metering.

### Cited Findings

**Kubernetes Jobs (feature maturity per docs)**
- `completionMode: Indexed` exposes `JOB_COMPLETION_INDEX`; `backoffLimitPerIndex` + `maxFailedIndexes` (stable v1.33; `backoffLimit` defaults to 2147483647 when set); `podFailurePolicy` (stable 1.31; requires `restartPolicy: Never`; actions `FailJob | Ignore | Count | FailIndex`; `onExitCodes`, `onPodConditions: [{type: DisruptionTarget}]` to not count preemption/eviction as failures); `spec.suspend` (stable v1.24; terminates active pods with SIGTERM, condition `Suspended`, `startTime` reset on resume so `activeDeadlineSeconds` restarts); `ttlSecondsAfterFinished` (stable v1.23); `successPolicy` (`succeededIndexes`, `succeededCount`); Elastic Indexed Jobs (stable v1.31); `podReplacementPolicy` `TerminatingOrFailed|Failed` (stable v1.34); `managedBy` (stable v1.35); JobSet as parent controller. Example: — [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/)
  ```yaml
  spec:
    backoffLimit: 6
    podFailurePolicy:
      rules:
      - action: FailJob
        onExitCodes: {containerName: main, operator: In, values: [42]}
      - action: Ignore
        onPodConditions:
        - type: DisruptionTarget
  ```

**Kubernetes Agent Sandbox (SIG Apps, kubernetes-sigs/agent-sandbox)**
- Declarative API for "long-running, stateful, singleton workloads"; CRDs `Sandbox` (single stateful Pod with stable hostname/network identity, optional persistent storage, create/scheduled-delete/pause/resume), `SandboxTemplate`, `SandboxWarmPool`, `SandboxClaim` (extensions module); isolation delegated to gVisor/Kata via `RuntimeClass`; Go SDK (`sigs.k8s.io/agent-sandbox/clients/go/sandbox`) and Python SDK (`pip install k8s-agent-sandbox`); optional Sandbox Router reverse proxy; install docs reference v1.0.2; Apache-2.0; ~4.3k stars; README contrasts with Deployments/StatefulSets (not Jobs). — [agent-sandbox README](https://github.com/kubernetes-sigs/agent-sandbox)
- Kubernetes blog post "Running Agents on Kubernetes with Agent Sandbox" (Janet Kuo, Justin Santa Barbara, 2026-03-20) frames the shift from ~50 ms stateless calls to long-running stateful agents. — [Kubernetes blog](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox)
- GKE "Agent Sandbox" is Google's managed controller for isolating LLM-generated code with process/storage/network isolation. — [GKE Agent Sandbox](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/agent-sandbox)

**Argo Workflows (runtime = Pod; plugin = sidecar)**
- Every template type (container, script, resource, http, plugin, suspend, dag/steps) runs through the controller; `activeDeadlineSeconds` at workflow level bounds runtime; `retryStrategy` can apply to all templates via WorkflowSpec; `podGC`, `ttlStrategy`, `synchronization` locks, `parallelism`. — [Argo fields](https://argo-workflows.readthedocs.io/en/latest/fields/)
- Infrastructure restarts (node eviction, disk pressure) are handled by a separate "Automatic Pod Restarts" mechanism, distinct from `retryStrategy`. — [Argo retries](https://argo-workflows.readthedocs.io/en/latest/retries/)
- Executor plugins poll via `requeue` (e.g. `"2m"`) with node phase `Pending`/`Running` — the recommended way to represent a long external job. — [Argo executor plugins](https://argo-workflows.readthedocs.io/en/latest/executor_plugins/)

**Tekton**
- Each Task runs as a Pod; per-Task `timeout` bounded by PipelineRun timeout (overridable at runtime via `taskRunSpecs`); step-level `timeout` kills the step and fails the TaskRun; sidecars start before steps and are deleted after. — [Tekton Pipelines](https://tekton.dev/docs/pipelines/pipelines/); [Tekton Tasks](https://tekton.dev/docs/pipelines/tasks/)

**Dagger**
- Latest engine/CLI release v0.21.10 (2026-09-30) with matching SDKs (TypeScript, Rust, Python, PHP, Go, Elixir) and Helm chart; v0.21.9 (2026-08-26) added SSHFS-backed volumes. Docs site shows a "1.0-beta" track plus 0.16–0.21. — [Dagger releases](https://github.com/dagger/dagger/releases); [docs.dagger.io](https://docs.dagger.io/)
- The CLI finds a container runtime on the host and starts the engine in a container (image version-matched to the CLI). — [Dagger container runtimes](https://docs.dagger.io/reference/container-runtimes/)
- LLM feature (0.21 docs): `LLM` core type; "Dagger can be used as a runtime and programming environment for AI agents"; `Env` binds typed inputs/outputs (Directory, Container, modules, strings); the LLM discovers and calls Dagger Functions as tools in an agent loop; providers OpenAI/Anthropic/Google plus local (Docker Model Runner, Ollama); modules can be exposed as MCP servers and external MCP servers attached via `withMCPServer`; end-to-end tracing of prompts/tool calls. — [Dagger LLM docs 0.21](https://docs.dagger.io/features/llm)
- Third-party: VibeKit uses Dagger as a local Docker-backed sandbox for agents; Docker Sandboxes kit runs the Dagger engine inside a microVM. — [VibeKit Dagger](https://docs.vibekit.sh/supported-sandboxes/dagger); [Docker sbx-kits-dagger](https://dhub.cyberranges.com/r/ajeetraina777/sbx-kits-dagger)

**GitHub Actions**
- Job `timeout-minutes` default 360; `GITHUB_TOKEN` max 24 h; per-minute runner rates: Linux 1-core $0.002, Linux 2-core x64 $0.006, Linux arm64 $0.005, Windows $0.010, macOS $0.062; included minutes Free 2,000 / Pro 3,000 / Team 3,000 / Enterprise Cloud 50,000; self-hosted runners free. — [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax); [GitHub Actions billing](https://docs.github.com/en/billing/managing-billing-for-your-products/about-billing-for-github-actions)
- Claude Code GitHub Action (`anthropics/claude-code-action@v1`): interactive (`@claude` trigger) vs automation (`prompt:` input) modes; inputs `prompt`, `claude_args` (e.g. `--max-turns 5 --model ... --allowedTools ...`), `anthropic_api_key` / `claude_code_oauth_token`, `github_token`, `plugins`, `settings`, `use_bedrock|use_vertex|use_foundry`; OIDC federation inputs (`anthropic_federation_rule_id`, `anthropic_organization_id`, …) need `id-token: write`; cost = Actions minutes + API tokens; docs recommend `--max-turns`, workflow timeouts and `concurrency` to cap runaway runs; GitLab CI variant exists. Example workflow: — [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions)
  ```yaml
  jobs:
    claude:
      if: contains(github.event.comment.body, '@claude')
      runs-on: ubuntu-latest
      permissions: {contents: write, pull-requests: write, issues: write, id-token: write, actions: read}
      steps:
        - uses: actions/checkout@v6
          with: {fetch-depth: 1}
        - uses: anthropics/claude-code-action@v1
          with:
            anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
  ```

**Airflow long-running / preemption**
- KubernetesPodOperator (provider 10.24.0): `on_finish_action` (`delete_pod` …), `on_kill_action` default `delete_pod`, `termination_grace_period` respected; `deferrable=True` with `logging_interval`; `reattach_on_restart` (default True) deprecated in favour of `durable` (default True) on Airflow 3.3+; XCom via sidecar writing `/airflow/xcom/return.json`; "The Kubernetes executor is **not** required to use this operator." — [Airflow CNCF Kubernetes operators](https://airflow.apache.org/docs/apache-airflow-providers-cncf-kubernetes/stable/operators.html)
- Task instance heartbeat timeouts replace "zombies"; causes include OOM kills, failed liveness probes, pod rescheduling; `execution_timeout`; retry policies (`ExceptionRetryPolicy`, `RetryRule`); sensors `reschedule` mode; `deferred` state; `awaiting_input` holds no worker/triggerer slot. — [Airflow tasks](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html)

**Buildkite**
- Agents poll; hosted agents are ephemeral and destroyed per job; `pre-bootstrap` can veto a job; hook failures after `command` can replace the exit code and affect retry rules. — [Buildkite agent](https://buildkite.com/docs/agent/v3); [Buildkite hooks](https://buildkite.com/docs/agent/v3/hooks)

### Inferences
- For hour-scale agent sessions, the K8s-native pattern is: one Pod/Sandbox per session + controller that polls (Argo `requeue`, Job status) + `podFailurePolicy`/`DisruptionTarget` to distinguish preemption from agent failure + `suspend`/pause for human waits. Hosted CI runners are poorly suited to multi-hour sessions because of fixed ceilings (GitHub 6 h default) and per-minute billing of the waiting runner.
- Cost attribution: hosted CI gives it for free (minutes × rate per job); on Kubernetes it must be reconstructed from pod labels (e.g. `workflows.argoproj.io/workflow`) with external metering — no engine above exposes per-step cost natively.
- Heartbeat semantics split into *runtime liveness* (kubelet probes, Nomad `Fingerprint`, Prefect worker heartbeat) and *task liveness* (Airflow task-instance heartbeat). Agent runtimes need the second kind because a healthy container can host a stalled agent loop.

### Gaps
- No source found for Argo-native cost attribution or for Buildkite job time limits/hosted-agent pricing.
- Dagger 1.0 GA date and the "sandboxed runtime" page content were not retrievable; only the 1.0-beta docs track and 0.21.10 release are confirmed.
- Kubernetes Agent Sandbox warm-pool latency numbers are in a linked tuning guide not fetched.

---

## KQ4. Which sandbox providers are designed for coding agents, and which features matter (git credentials, persistent workspace, snapshot/fork, egress rules, pricing)?

### Takeaway
By October 2026 the field splits into (i) microVM sandbox APIs built for agents — E2B, Daytona, Blaxel, Runloop, Fly Sprites, Northflank, Modal Sandboxes — all offering per-second billing, pause/snapshot, and domain/CIDR egress control; (ii) vendor-integrated agent runtimes — Anthropic (Claude Code cloud sessions, Managed Agents, open-source `srt`), OpenAI Codex cloud/CLI — whose distinguishing feature is *credential-free sandboxes* (git/API keys injected by a proxy outside the VM); and (iii) Kubernetes-native (Agent Sandbox CRDs, Northflank BYOC). Memory-snapshot pause/resume (E2B, Blaxel, Fly, Daytona VM class) and proxy-side secret injection are the two features that have become table stakes.

### Cited Findings

**E2B**
- Sandbox = "A fast, secure Linux VM created on demand for your agent"; pause saves filesystem *and* memory; "paused sandboxes are kept indefinitely until you kill them"; SDKs JS/TS (`e2b`) and Python; `sandbox.commands.run()`. — [E2B docs](https://docs.e2b.dev/)
- Persistence: states Running/Paused/Snapshotting/Killed; `sandbox.pause()`, `Sandbox.connect()` resumes; `mode: 'filesystem'` for fs-only pause; auto-pause via `lifecycle.onTimeout: "pause"` (`autoResume` default false); pause ≈4 s per GiB RAM, resume ≈1 s; default timeout 5 min; continuous runtime cap 24 h (Pro) / 1 h (Hobby), reset by pause/resume; pause refusals return 503 → `ServiceBusyError` (SDK ≥2.48.0). — [E2B persistence](https://docs.e2b.dev/docs/sandbox/persistence)
- Network: outbound internet on by default; `allowInternetAccess=false` ≡ deny `0.0.0.0/0`; `network.allowOut/denyOut` accept IPs, CIDRs, domains (domains allow-list only; wildcard `*.x`; HTTP Host / HTTPS SNI on 80/443 only; QUIC unsupported; 8.8.8.8 auto-allowed); allow beats deny; per-host header-injection rules with `Secret.fill` resolved by the egress proxy outside the sandbox (limits: 10 domains, 1 rule/domain, 20 headers/rule); `updateNetwork` replaces config at runtime; bring-your-own SOCKS5 proxy. — [E2B internet access](https://docs.e2b.dev/docs/sandbox/internet-access)
- Pricing: Hobby $0 (+$100 one-time credits, 20 concurrent, 1 h sessions, 10 GiB); Pro $150/mo (100 concurrent, 24 h sessions, 20 GiB); Pro+ $650 (600 concurrent) / $1,150 (1,100 concurrent); Enterprise $3,000/mo minimum. Usage: $0.000014 per vCPU-s (~$0.0504/vCPU-h), $0.0000045 per GiB-s (~$0.0162/GiB-h); storage free; sizes 1–8 vCPU / 1–8 GiB; default 2 vCPU/4 GiB ≈ $0.166/h. — [E2B pricing](https://e2b.dev/pricing)

**Modal Sandboxes**
- `Sandbox.create` with Image, Volumes, Secrets (as env vars), `runtime="gvisor"` (default) or `"vm"` (own kernel, for Docker/FUSE); GPU only with gVisor; default lifetime 5 min, `timeout` up to 24 h; `idle_timeout`; lifecycle Created→Scheduled→Started→Ready→Finished; `exec` with per-process returncode; readiness probes; Filesystem Snapshots for >24 h work (VM snapshots include `/var/lib/docker`); tags, `from_id/from_name`. — [Modal sandbox guide](https://modal.com/docs/guide/sandbox)
- Network: outbound to public IPs allowed by default; `block_network=True`; `outbound_cidr_allowlist`; `outbound_domain_allowlist` (Beta, TLS/443 SNI-based, `*.` wildcards; domain fronting caveat → sidecar proxy); alpha runtime policy update (`_experimental_set_outbound_network_policy`); `inbound_cidr_allowlist`; tunnels (`encrypted_ports`, `unencrypted_ports`, `h2_ports`); Sandbox Connect Tokens. — [Modal sandbox networking](https://modal.com/docs/guide/sandbox-networking)
- Pricing (page notes policy effective 2026-09-01): Sandboxes/Notebooks CPU $0.00003942/core-s (~$0.142/core-h), memory $0.00000667/GiB-s (~$0.024/GiB-h), GPU at standard rates (H100 ~$3.95/h); plans Starter $0 (+$30/mo credits, 100 containers), Team $250/mo (+$100 credits, 5,000 containers), Enterprise custom; per-second billing. — [Modal pricing](https://modal.com/pricing)

**Daytona (docs v0.224)**
- "under 90ms" startup (container class); SDKs Python, TypeScript, Ruby, Go, Java; REST + CLI + Toolbox API (`process/code-run`); Snapshots, Volumes, Regions, Declarative Builder. — [Daytona docs](https://www.daytona.io/docs/)
- Sandbox creation: from named snapshot (`daytona-small` 1 vCPU/1 GiB/3 GiB … `daytona-large` 4/8/10) or pinned image (floating tags rejected); resources `cpu/memory/disk/gpu/gpu_type`; env vars (secrets recommended); labels; `target` `us|eu`; `ephemeral=True`; `kvm=True` (nested virt in Linux VM class); `spot=True` for preemptible GPU; classes Container (default), Linux VM / Windows VM (support fork, pause/resume, snapshot-from-sandbox), macOS via use.computer, GPU (H100/H200/B300/RTX/MI355X); org max non-GPU 4 vCPU/8 GiB/10 GiB; spot sandboxes can be terminated without warning (`spotEvictedAt`). — [Daytona sandboxes](https://www.daytona.io/docs/en/sandboxes)
- Network: `networkAllowList` (IPv4 CIDR, ≤10), `domainAllowList` (≤100, `*.` wildcards), `networkBlockAll`, `outboundProxyUrl` (sets `HTTP_PROXY/HTTPS_PROXY`); Tiers 1–2 locked to org policy with "essential services" (registries, git hosts, model providers) always reachable; Tiers 3–4 full internet by default and sandbox-level overrides allowed (`WRITE_SANDBOXES` permission), live updates without restart. — [Daytona network limits](https://www.daytona.io/docs/en/network-limits)
- Billing: pay-as-you-go on reserved vCPU/RAM/disk; stopped = disk only; paused (VM) = disk only; archived (container) = not billed; snapshots billed for storage; free credits not usable for GPU. — [Daytona billing](https://www.daytona.io/docs/en/billing)

**Fly.io Machines / Sprites**
- Machines: "fast-launching VMs with a simple REST API"; states created/started/stopped/destroyed (start "usually... well under a second"); auto-stop on exit; volumes for persistence; placement best-effort, clients must retry. — [Fly Machines overview](https://docs.fly.io/machines/overview)
- API: `POST /v1/apps/{app}/machines` (only `config.image` required; `skip_launch`, `lease_ttl`), update requires full config, `GET .../wait?state=started|stopped|suspended|destroyed` (60 s default), `start`, `stop` (signal default SIGINT + timeout), **`suspend` (snapshots state incl. memory; resume from snapshot attempted, not guaranteed; stopping a suspended machine invalidates the snapshot)**, `DELETE` (`force`), leases (`fly-machine-lease-nonce`), cordon/uncordon, metadata; config `auto_destroy`, `restart.policy` (`no|on-failure|always`, `max_retries`), `stop_config{signal,timeout}`. — [Fly Machines API](https://docs.fly.io/machines/api/machines-resource)
- Sprites (sprites.dev, launched Jan 2026): "Stateful sandbox environments with checkpoint & restore"; persistent Linux environments; checkpoints ~300 ms on NVMe (one report: last five retained); MCP endpoint `sprites.dev/mcp`, Go/TS SDKs; launch post argues ephemeral sandboxes are obsolete; a competitor (Blaxel) cites second-range restore latency. — [Simon Willison on Sprites](https://simonwillison.net/2026/Jan/9/sprites-dev/); [Fly "Code And Let Live"](https://www.fly.io/blog/code-and-let-live/); [Thoughtworks Radar](https://www.thoughtworks.com/radar/platforms/sprites); [Blaxel comparison](https://blaxel.ai/blog/fly-io-sprites-alternatives-ai-agent-sandboxes)

**Northflank**
- Per-workload microVM via Kata Containers or gVisor (Firecracker/Cloud Hypervisor mentioned on other pages); "Sub-second cold starts"; persistent volumes 4 GB–64 TB; no forced time limits ("seconds or weeks"); GPUs (H100 $2.74/h "all-in"); BYOC to AWS/GCP/Azure/Oracle VPCs; CPU $0.01667/vCPU-h; SOC 2 Type 2, HIPAA; claims millions of microVMs/month since 2021. — [Northflank sandboxes](https://northflank.com/product/sandboxes); [Northflank blog](https://northflank.com/blog/agent-sandbox-on-kubernetes)
- Fly.io's comparison says Northflank is the stronger pick when a GPU inside the sandbox or BYOC is required, and describes it as Kubernetes-based with sandboxes as one workload type. — [Fly.io comparison](https://fly.io/learn/top-northflank-alternatives-for-secure-ai-code-execution-and-sandboxed-environments.md)

**Runloop**
- "batteries included platform designed for building and optimizing AI-driven software engineering agents": Devboxes (isolated cloud VMs, "a few seconds" to first command), Blueprints (Dockerfile-based images), Snapshots (disk), Axons (event streams), Network Policies (egress restriction, `network_policy_id`), Agent Gateways (LLM API proxy so agents never see credentials), MCP Hub; starter image has Python 3.12, Node 22.15, `gh`; SDKs Python/TS, CLI. — [Runloop overview](https://docs.runloop.ai/); [Runloop devboxes](https://docs.runloop.ai/docs/devboxes/overview)
- Lifetime: default max lifetime **1 hour** (`keep_alive_time_seconds`), or `lifecycle.after_idle{idle_time_seconds, on_idle: shutdown|suspend}` (don't mix). — [Runloop lifetime](https://docs.runloop.ai/docs/devboxes/start-stop)
- Docs index also lists: code mounts (clone GitHub repos at creation incl. private), account secrets injected as env vars, `exec`/`exec_async` with streaming logs, SSH, tunnels with wake-on-HTTP, suspend/resume-while-waiting-for-PR tutorial, deploy-agent GitHub Action. — [Runloop llms.txt](https://docs.runloop.ai/llms.txt)
- Pricing: Basic $0 + usage; Pro $250/mo + usage (suspend/resume, repo connections); trial $50 credits (3 devboxes, 5 blueprints, 10 snapshots); CPU $0.108/CPU-h, memory $0.0252/GB-h, blueprint build $0.252/h, devbox storage $0.00034/GB-h, snapshot storage $0.000072/GB-h; suspended = storage only. — [Runloop pricing](https://www.runloop.ai/pricing)

**Blaxel**
- Sandboxes with REST file/process API + built-in MCP server; Active → Standby ~15 s after last connection, resume "under 25 ms", standby snapshots full state incl. memory and processes (external TCP connections not preserved); Archive = filesystem-only; EROFS base image + tmpfs overlay (~50% RAM reserved); Volumes and Agent Drive; reserved ports 80/443/8080; proxy/network controls incl. bound egress IPs; pricing = memory + storage while active (CPU not billed separately), standby = storage only; SDKs TS (`@blaxel/core`), Python, Go; in-place upgrades need sandbox API ≥v0.2.0 (created after 2026-02-02); tutorial connects Claude Agent SDK via MCP. — [Blaxel sandboxes](https://docs.blaxel.ai/Sandboxes/Overview)

**Anthropic — `sandbox-runtime` (srt), Claude Code sandbox, devcontainer, cloud sessions, Managed Agents**
- `@anthropic-ai/sandbox-runtime` (Apache-2.0, "Beta Research Preview", built for Claude Code): macOS `sandbox-exec`/Seatbelt profiles; Linux bubblewrap + network-namespace removal + seccomp blocking Unix sockets; Windows alpha (dedicated `srt-sandbox` user, WFP egress fence); network default-deny via host-side HTTP + SOCKS5 proxies over bind-mounted Unix sockets; settings `~/.srt-settings.json` with `network.allowedDomains/deniedDomains/allowUnixSockets/allowLocalBinding`, `filesystem.denyRead/allowRead/allowWrite/denyWrite` (reads allowed by default, writes denied by default; `allowRead` overrides `denyRead`, `denyWrite` overrides `allowWrite`); CLI `srt "<cmd>"`, library `SandboxManager.initialize/wrapWithSandbox/reset`. — [sandbox-runtime README](https://github.com/anthropic-experimental/sandbox-runtime)
- Claude Code built-in Bash sandbox: OS-enforced around Bash/PowerShell/Monitor commands only (file tools, MCP, hooks run outside); macOS Seatbelt; Linux/WSL2 bubblewrap + socat (+ optional seccomp from the srt package); writes limited to cwd, temp and added dirs; reads deny-listable (`~/.ssh`, `~/.aws/credentials`); no direct network route — proxy with `allowedDomains` (starts empty), `deniedDomains`, `strictAllowlist`, `allowManagedDomainsOnly`, `excludedCommands`, `autoAllowBashIfSandboxed` (default true), `allowUnsandboxedCommands`/`failIfUnavailable`; **credential masking**: sandboxed commands see a per-session sentinel and the proxy substitutes the real value on allowed hosts (`injectHosts`), requires `network.tlsTerminate`; SigV4 re-signing (v2.1.221+); proxy refuses hostnames resolving to loopback/link-local (169.254.169.254) unless allowed. — [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing)
- Dev container: feature `ghcr.io/anthropics/devcontainer-features/claude-code:1.0`; reference container adds `init-firewall.sh` (needs `NET_ADMIN`, `NET_RAW`) limiting egress to allowed destinations; persist `~/.claude` via named volume + `CLAUDE_CONFIG_DIR`; `/etc/claude-code/managed-settings.json` for policy; `--dangerously-skip-permissions` allowed only as non-root; warning that with that flag a malicious project can exfiltrate anything in the container incl. `~/.claude` credentials — "Avoid mounting host secrets such as `~/.ssh`". — [Claude Code devcontainer](https://code.claude.com/docs/en/devcontainer)
- Cloud sessions: each session in an isolated Anthropic-managed VM (Ubuntu 24.04 x86_64, repo cloned, toolchains + Docker pre-installed) or a self-hosted environment; **GitHub credentials "stay encrypted on Anthropic's servers and never enter a session's VM"** — a GitHub proxy attaches them server-side (`GH_TOKEN` reads `proxy-injected`); network secrets (Pro/Max) likewise attached by an agent proxy to listed hosts; access levels Trusted (allowlisted registries/GitHub/cloud SDKs), Custom (own list, `*.` wildcards, optional "include default list"), None; setup script cached as a filesystem snapshot only if it finishes in ~5 min, cache expires ~7 days; command timeouts: 2 min foreground default (up to 10), background up to 30 min (`BASH_DEFAULT_TIMEOUT_MS`), SessionStart hook 600 s; sessions expire after inactivity and VM is reclaimed (history restored, background work lost); auto-fix PRs; `claude --cloud "..."` and `claude -p "msg" --cloud <session-id>`; no separate VM charge, counts against plan usage. — [Claude Code in the cloud](https://code.claude.com/docs/en/claude-code-on-the-web); [Cloud environments](https://code.claude.com/docs/en/cloud-environments)
- Claude Managed Agents (beta, header `managed-agents-2026-04-01`): concepts Agent / Environment ("an Anthropic-managed cloud sandbox, or a self-hosted sandbox on your own infrastructure") / Session (persistent filesystem + history, "resume cleanly after pauses") / Events (SSE); built-in Bash, file ops, web search/fetch with domain allow/blocklists, MCP; scheduled deployments; not ZDR/HIPAA eligible; also on Claude Platform on AWS. — [Managed Agents overview](https://platform.claude.com/docs/en/managed-agents/overview)
- Third-party reports (unverified): public beta April 2026; self-hosted sandboxes public beta May 2026; one source cites $0.08 per session-hour fee. — [noqta.tn](https://noqta.tn/en/blog/anthropic-self-hosted-sandboxes-claude-agents-enterprise-guide-2026); [analyticsvidhya](https://www.analyticsvidhya.com/blog/2026/06/claude-managed-agents/)

**OpenAI Codex (cloud + CLI)**
- Cloud: each task has its own workspace and keeps running while your computer sleeps; environments have setup ("install") script, maintenance script, env vars, network secrets, OIDC connections; container state cached up to 12 h (invalidated when setup/maintenance scripts, env vars or secrets change; shared across Business/Enterprise users); secrets available only to setup scripts and removed before the agent phase; setup scripts run in a separate bash session. — [Codex cloud](https://learn.chatgpt.com/docs/cloud); [Codex cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environment.md)
- Internet access: "By default, Codex blocks internet access during the agent phase"; setup scripts keep access; On mode limited by domain allowlist (presets None / Common dependencies / All) and allowed HTTP methods (recommend GET, HEAD, OPTIONS only); risks listed: prompt injection, exfiltration, malware, licensing. — [Codex internet access](https://learn.chatgpt.com/docs/cloud/internet-access)
- CLI sandboxing: macOS Seatbelt "out of the box"; Linux/WSL2 bubblewrap (bundled helper needs unprivileged user namespaces; Ubuntu 24.04 may need `bwrap-userns-restrict` AppArmor profile); Windows native sandbox; modes `read-only`, `workspace-write` (default low-friction), `danger-full-access`; `sandbox_workspace_write.writable_roots`; approval policies `on-request`, `never` (`untrusted` retired), `approvals_reviewer = user|auto_review`; CLI flags `--sandbox workspace-write --ask-for-approval on-request`. — [Codex sandboxing](https://learn.chatgpt.com/codex/sandboxing)

### Inferences
- Features that matter for pipeline-step use, and who has them: **memory snapshot pause/resume** (E2B, Blaxel, Fly suspend/Sprites, Daytona VM class, Runloop suspend, Modal fs-only); **fork from snapshot** (Daytona VM, Fly Sprites checkpoints, E2B/Runloop/Blaxel via image snapshots); **proxy-side secret injection so the agent never sees keys** (Claude Code cloud + sandbox masking, E2B per-host rules with `Secret.fill`, Runloop Agent Gateways, Codex network secrets); **domain-level egress allowlists** (all except Northflank where docs were silent); **no hard lifetime ceiling** (Northflank, Daytona VM, Blaxel higher tiers) vs ceilings (E2B 24 h continuous, Modal 24 h, Runloop 1 h default, GitHub 6 h).
- Pricing models cluster at $0.05–0.15 per vCPU-h and $0.008–0.025 per GiB-h, billed per second, with paused/stopped state billed as storage only — comparable to or cheaper than GitHub-hosted Linux runners ($0.006/min ≈ $0.36/h for 2 cores).
- For a Kora-style runtime abstraction, the common provider verbs are: `create(image|snapshot, resources, env, network)`, `exec(cmd, timeout) → {stdout, exit}`, `files.read/write`, `pause/resume` (or `suspend/start`), `snapshot/fork`, `kill`, plus `updateNetwork`. These map cleanly onto the KQ1 runtime interface.

### Gaps
- Daytona per-vCPU/GiB rates and Tier limits were not on the fetched pages (Limits page not fetched).
- Fly Machines `exec`/`signal` endpoints, suspend restrictions and Sprites pricing not retrieved from primary docs.
- Runloop network-policy configuration details (allowlist syntax) not fetched (page 404 at guessed URL; index lists `/docs/network-policies`).
- Modal filesystem/memory snapshot API details were in a page not fetched.
- Northflank docs for sandbox snapshots, network policies and secrets not located (product page silent).
- Codex cloud task duration limit and underlying container image name not stated in fetched docs (third-party pages mention `codex-universal`).
- Managed Agents pricing not confirmed from Anthropic docs.

---

## KQ5. What are typical patterns for Docker-based local runtime vs Kubernetes runtime inside a single framework?

### Takeaway
Frameworks separate *where the orchestrator worker lives* from *where the task body runs*: Airflow offers both an executor-level choice (`KubernetesExecutor` = every task in its own pod) and an operator-level choice (`KubernetesPodOperator` usable with any executor); Prefect and Dagster expose the choice as interchangeable backends (work-pool type / run launcher) sharing one job-configuration template; Dagster additionally has a per-run launcher and a per-step executor; Buildkite and GitHub keep one agent protocol and let the agent be a VM, container or Kubernetes pod.

### Cited Findings
- Airflow: `KubernetesExecutor` is a "containerized" executor where each task runs in its own pod; `LocalExecutor` runs in the scheduler process; executors can be mixed per task since 2.10 with `executor=` on the operator; two instances of the same executor class only in multi-team Airflow. — [Airflow executors](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/executor/index.html)
- Airflow: "The Kubernetes executor is **not** required to use this operator" (KPO); `pod_template_file` ranks below explicit KPO args; XCom via sidecar. — [Airflow CNCF Kubernetes operators](https://airflow.apache.org/docs/apache-airflow-providers-cncf-kubernetes/stable/operators.html)
- Prefect work pools: Process (subprocess on worker), Docker ("require access to a running Docker daemon"), Kubernetes ("jobs scheduled on a Kubernetes cluster"), ECS, ACI, Cloud Run/V2, Vertex AI, Coiled, Prefect Managed; push pools (Cloud Run, ECS, ACI, **Modal**) need no worker; queues have priority (1 = highest) and concurrency limits; queue READY if polled in last 60 s. — [Prefect work pools](https://docs.prefect.io/v3/concepts/work-pools)
- Prefect custom worker: one `BaseJobConfiguration` Pydantic model doubles as the base job template's `variables` JSON schema (`BaseVariables`), so Docker vs Kubernetes differ only in the config fields and the `run()` body. — [Prefect custom worker](https://docs.prefect.io/v3/advanced/developing-a-custom-worker)
- Dagster: run launcher (`DefaultRunLauncher` process, `DockerRunLauncher`, `K8sRunLauncher` Job-per-run, `EcsRunLauncher`, `CeleryK8sRunLauncher`) chooses the run worker; the executor inside it (`celery_k8s_job_executor` mentioned) chooses per-step compute. — [Dagster run launchers](https://dagster.io/docs/deployment/execution/run-launchers)
- Nomad: one driver interface with `Capabilities()` declaring fs/network isolation modes; `raw_exec` as config example, `exec2` as external reference driver. — [Nomad task drivers](https://developer.hashicorp.com/nomad/docs/concepts/plugins/task-drivers)
- Buildkite: same hook model on VMs and on the Agent Stack for Kubernetes, except hooks run in separate checkout/command containers and env exported in checkout is not visible to command containers. — [Buildkite hooks](https://buildkite.com/docs/agent/v3/hooks)
- GitHub: a job can run on a hosted/self-hosted runner, inside `container:` with `services:`, or as a reusable workflow; container jobs require Linux runners. — [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- Modal Sandboxes choose runtime per sandbox: `runtime="gvisor"` vs `"vm"`. — [Modal sandbox guide](https://modal.com/docs/guide/sandbox)
- Daytona sandbox classes: Container (default), Linux VM, Windows VM, GPU, macOS. — [Daytona sandboxes](https://www.daytona.io/docs/en/sandboxes)

### Inferences
- Three layers to keep separate in a runtime abstraction: (1) *scheduler/worker placement* (Airflow executor, Prefect worker, Dagster daemon), (2) *task body isolation* (DockerOperator/KPO, Prefect job config, Dagster launcher, Nomad driver), (3) *intra-task fan-out* (Dagster executor, Argo DAG). Agent sessions are layer 2 objects; the Docker-local vs Kubernetes decision belongs there and should be expressed as one typed config object (Prefect's pattern) rather than separate operators.
- Providers now add a fourth axis — microVM vs gVisor vs container *class* — selectable per sandbox (Modal, Daytona), which a runtime protocol should surface as a capability flag (cf. Nomad `Capabilities()`).

### Gaps
- No Airflow doc page fetched explicitly contrasts `DockerOperator` with KPO; the DockerOperator provider docs were not retrieved.
- Prefect base-job-template JSON example not captured.
