# States, colours and words

Status: design detail behind [docs/console.md](../../../docs/console.md), written 2026-10-16. The internal state machines are in [data-model.md](../../specs/data-model.md) section 4; this file maps each of them to what the console shows.

## The five colours

| colour | meaning | whose move |
|---|---|---|
| gray | not started, ready, skipped, upstream failed, cancelled; the last three carry a mark | nobody's yet, or nobody's any more |
| blue | running, including every wait that is not a person's: timer, event, sensor poll, retry back-off, the queue for a worker, admission | the system's or the outside world's |
| amber | waiting for a person: an open approval request | the person's |
| green | succeeded | done |
| red | failed | stopped |

Error classes never colour a node; they are words in the second line. The five colours are defined as light and dark pairs and checked for contrast in both themes.

## Node instances

| `node_instance.status` | colour | word | second line (zoomed in) |
|---|---|---|---|
| `pending`, `ready` | gray | `pending` | nothing |
| `queued` | blue | `running` | "in queue · <since the attempt was created>" |
| `running` | blue | `running` | "<elapsed since the attempt started>" |
| `waiting`, wait kind `approval` | amber | `waiting for you` | "approval · <waiting for> of <deadline>" |
| `waiting`, wait kind `timer` | blue | `running` | "timer · <remaining>" |
| `waiting`, wait kind `event` or `poll` | blue | `running` | "waiting for <event name or sensor>" |
| `waiting`, wait kind `retry_backoff` | blue | `running` | "retry in <remaining> · attempt n of m" |
| `waiting`, wait kind `admission` | blue | `running` | "waiting for admission · <reason: concurrency limit, daily budget, target held by run …>" |
| `succeeded` | green | `succeeded` | "in <duration>" |
| `failed` | red | `failed` | the error class in one word (table below) |
| `skipped` | gray, marked | `skipped` | "branch not taken" or "trigger rule" |
| `upstream_failed` | gray, marked | `upstream failed` | "<upstream node> failed" |
| `cancelled` | gray, marked | `cancelled` | "by <actor>" |

The wait kind, the deadline, the subscription and the admission reason come from the node's current wait as [ADR-0015](../../../docs/decisions/0015-web-console-design.md) exposes it.

A node whose declared definition is materialised per fan-out element is one rectangle: its colour is the worst state among its instances in the order red, amber, blue, green, gray, and the strip inside it carries one segment per element in that element's colour.

## Error classes in the second line

| `error_class` | word |
|---|---|
| `harness_error` | harness error |
| `command_failed` | command failed · exit <code> |
| `timeout` | timeout |
| `events_exhausted` | event limit |
| `budget_exhausted` | budget exhausted |
| `artifact_invalid` | invalid output |
| `attempt_failed` | sub-graph attempt failed · <else_reason in brief> |
| `approval_rejected` | rejected |
| `approval_expired` | approval expired |
| `candidate_moved` | candidate moved |
| `wait_expired` | wait expired |
| `stalled` | stalled |
| `protected_path_modified` | protected path modified |
| `secret_leak` | secret leak |
| `infrastructure` | infrastructure · <branch left on the code host, when a push preceded the failure> |
| `cancelled` | cancelled |
| `config_error` | configuration error |

A node under the runtime's infrastructure retry policy is blue with "worker lost · retry n of m" until the policy is exhausted, then red with "infrastructure".

## Attempts (in the node panel)

| `attempt.status` | shown as |
|---|---|
| `created`, `claimed` | waiting for a worker; the worker when claimed; the lease expiry |
| `submitted`, `running` | running on <worker>; last heartbeat; `desired_state` when `cancel` |
| `finishing` | collecting: artifacts being validated, the publish plan running; a repeated publish on an unknown outcome is listed from its event |
| `succeeded`, `failed`, `cancelled` | the terminal word with the error class |
| `lost` | lost: the runtime no longer knows the attempt; the infrastructure retry that followed |

## Runs

The run's colour and word in the header, on the runs list and in the traffic light are derived: red `failed`; green `succeeded`; gray `cancelled`; amber `waiting for you` when the run is `running` and its only live node instances wait for a person; otherwise blue `running` (`pending` is shown as `running` with "scheduling").

## Approval requests

| `approval_request.status` | on the review page and in Approvals |
|---|---|
| `pending` | amber; the deadline from the wait |
| `approved` | green; approver, channel, time |
| `rejected` | the node is red "rejected"; the response and its comments |
| `expired` | the node is red "approval expired" or gray `skipped` per `on_expiry` |
| `invalidated` | the banner with the bound commit and the observed head; the node is red "candidate moved" |
| `cancelled` | gray; the run or node was cancelled |

## Attention states

The six states the architecture critique asked the console to keep apart ([architecture-critique-2026-10-16.md](../../reviews/architecture-critique-2026-10-16.md), "Priorities for the user-experience design"), each with what the console knows, what it does not, and the permitted action.

| state | where it shows | known | not known | permitted action |
|---|---|---|---|---|
| pending review | amber node and run; the Approvals waiting block; the review page | the bound commit, the facts and their provenance, the cost, the deadline | whether a reviewer on the code host has started | approve or reject on the review page; cancel |
| stale evidence | red node "candidate moved"; the review page banner; the Approvals history | the bound commit, the observed head, the producing node to retry | what the new push contains | retry the producing node with feedback |
| exhausted budget | red node "budget exhausted"; for a sub-graph, the element's segment | the budget, the spend, the attempt that crossed it | whether a larger budget would have succeeded | retry on a new graph version by a rerun; cancel |
| unknown publish outcome | the attempt in `finishing` with the event of a repeated publish; after an infrastructure failure, the red node naming the branch | the deterministic branch name, the commit when captured, which worker repeats the plan | whether the change request exists until the search before create answers | wait; after an infrastructure failure, retry the node; cleanup of the branch is the graph author's command node |
| lost runtime | blue node "worker lost · retry n of m"; the timeline `attempt.lost` and `attempt.claimed`; the worker in System highlighted | the worker, when its lease expired, when the attempt became lost, how many infrastructure retries remain | whether the session is still running on an unreachable host | wait for the policy; cancel; restart the worker outside kora |
| incompatible resume | the attempt's `strategy_fallback` in the panel and in the comparison | that `resume` fell back to `fresh_with_feedback` and why (runtime capability, missing handle, changed harness version) | what the lost session state held | nothing; the fallback is the designed behaviour |
