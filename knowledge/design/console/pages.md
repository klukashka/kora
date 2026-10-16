# Console pages, element by element

Status: design detail behind [docs/console.md](../../../docs/console.md), written 2026-10-16. Every element names the API route that serves it ([api-and-agent-surface.md](../../specs/api-and-agent-surface.md) section 2); an element that needed a route change points at [ADR-0015](../../../docs/decisions/0015-web-console-design.md). States, colours and words are in [states.md](states.md); the pilot scenarios in [pilot-scenarios.md](pilot-scenarios.md).

## Frame

| element | content | route |
|---|---|---|
| top bar | namespace switcher (the token's namespace list; the last chosen namespace remembered in the browser; the first namespace on first entry), the sections Graphs · Targets · Approvals · Cost · System, the user and the theme switch | `GET /namespaces/{ns}` for each namespace of the token |
| page | full width below the bar; no side rail | |

## Graphs (the entry)

One card per graph of the namespace; a namespace with one graph opens its page directly.

| element | content | route |
|---|---|---|
| name and description | `graph.name`; the one-line `description` of the latest active version's document | `GET /namespaces/{ns}/graphs` |
| node count | declared top-level nodes of the latest active version; a `fan_out` or `subgraph` node counts once | the graphs list summary (ADR-0015) |
| traffic light | over the graph's runs in `running` or `failed`: runs whose only live work waits for a person; runs in `failed`; runs running otherwise; zeros hidden | the graphs list summary (ADR-0015) |
| tokens today | the sum of `usage_record` tokens over the graph's attempts started today in the namespace's timezone; the USD estimate beside it unless the billing class is `subscription` | the graphs list summary (ADR-0015) |
| click | the graph page | |

Not on the card: triggers and pause state, the last run, the version and its deploy date.

## Graph page

| element | content | route |
|---|---|---|
| runs list (left) | the graph's runs newest first; per row a strip of the top-level node instances' states, `target_key` (or the run's parameters in brief when the graph declares no target), `started_at` relative, the trigger kind and actor; the newest run selected by default | `GET /namespaces/{ns}/runs?graph=` with the top-level node states of each run |
| run header | target, state word and elapsed time, trigger and actor, tokens and USD so far, graph version number, `rerun_of` and the run's reruns, the Run button | `GET …/runs/{run}`, `GET …/runs?rerun_of=`, `GET …/runs/{run}/cost` |
| graph | the pinned version's topology laid out once from the IR; nodes as same-size rectangles; edges from `inputs.from` and `depends_on` | `GET …/graphs/{graph}/versions/{gv}`, `GET …/runs/{run}` for instance states, `GET …/runs/{run}/events?follow=true` for live changes |
| node, zoomed out | the kind icon; the rectangle's colour is the state | |
| node, zoomed in | the icon in a corner; the node name; the time-by-state line ([states.md](states.md)); "attempt n" when the instance has more than one attempt | the node instances of the run view; the wait of a waiting node (ADR-0015) |
| parallel instances | one rectangle for a declared node materialised per fan-out element; colour by the worst instance state; a strip with one segment per element in index order | the run view's node instances grouped by declared node id |
| fan-out node | collapsed: one rectangle with the strip and the summary "n elements: a accepted · b failed · c running"; expanded in place: a frame with the body drawn once, each body node carrying the strip; only the current `subgraph_attempt` of each element is drawn | the run view's node instances with `node_path` and `subgraph_attempt` |
| node panel | in order: state and reason with the `error` text; attempts (current expanded: harness and model from the resolved config, `strategy`, `strategy_fallback`, `started_at`, `finished_at`, tokens and USD; earlier attempts collapsed to a line with status and error class; for a fan-out element the earlier sub-graph attempts with their `else_reason`); artifacts of the current attempt with type, digest and payload link; links to the logs page and the comparison page; a human-input node: the review button on top and the request's state; a failed node: the retry button | `GET …/nodes/{node_path}`, `…/attempts[/{att}]`, `GET …/artifacts/{art}[/payload]`, `GET …/runs/{run}/cost` |
| timeline (bottom) | collapsed: "last event · when"; expanded: the run's events newest first with a filter by node; a clicked event highlights its node | `GET …/runs/{run}/events?follow=true` |
| Run button | a form from the graph's `params` JSON Schema (titles, descriptions, required marks); submits with a fresh idempotency key on the latest active version; the new run appears first in the list and is selected | `POST /namespaces/{ns}/graphs/{graph}/runs` |
| retry dialog | "retry <node> as <strategy from the node's policy>"; the list of node instances that clear-downstream will reset (the downstream closure of the run view); the strategy under a details disclosure, `resume` labelled as falling back on runtimes without `supports_resume` | `POST …/nodes/{node_path}/retry` |
| cancel | in the run header (the run) and in the node panel (the node); the confirmation names the running attempts that stop; the grace period comes from configuration | `POST …/runs/{run}/cancel`, `…/nodes/{node_path}/cancel` |

## Review page

Opens from a human-input node and from Approvals; one approval request.

| element | content | route |
|---|---|---|
| header | the bound commit, the branch and the change-request link from the bundle's `VcsRef`, the run's `target_key`, the request's deadline | `GET …/approvals/{apr}`; the deadline from the node's wait (ADR-0015) |
| facts: metrics | one table baseline → candidate → gain; every value followed by its provenance word: "measured · <command node>" or "reported by the agent · <agent node>" | `ReviewBundle.facts` |
| facts: gates | one row per gate: name, exit code, "verified" against the protected paths in force or "unprotected" | `ReviewBundle.facts` |
| facts: cost | `run_cost` per model with the weakest completeness | the request view |
| facts: change | changed paths and diff statistics; the diff itself as a link to the change request on the code host | `ReviewBundle.facts` |
| narrative | the agent's `summary` and `rationale`, labelled "written by the agent", set in the serif typeface | `ReviewBundle.narrative` |
| form | rendered from the request's JSON Schema; the submit button names the bound commit; without the `approve` flag the button explains the missing right when pressed | `POST …/approvals/{apr}/respond` with `commit_sha` and `digests` as shown |
| stale response | on `API006` the page reloads the request | |
| invalidated request | a banner with `bound_commit_sha` and `observed_head_sha`, the statement that nothing can be approved on this evidence, no form, the facts and narrative kept as history, the retry button for the producing node (named by the API's `API002` hint) with `fresh_with_feedback`, a link to that node | `GET …/approvals/{apr}`, `POST …/nodes/{node_path}/retry` |
| approved then moved | both commits, the request kept `approved`, the node failed with `candidate_moved` | the request view and the node view |

## Targets

| element | content | route |
|---|---|---|
| list row | the key; the strip of the holding run's top-level node states (the newest holder under `allow`; "idle" in gray without active work); the accepted change (date and commit, or a dash); the number of deferred runs when any; the list shows active targets by default with a switch to all | `GET /namespaces/{ns}/targets?active=true`, the runs' node states |
| target page: now | the holding run and `target_held_since`, its state word and the link to the review page when it waits for a person; the deferred runs with `run.target_deferred` times | `GET /namespaces/{ns}/targets/{key}` |
| target page: accepted | the latest `approved` request: commit, change-request link, `approver`, time; the three facts "accepted · merged · verified" with their sources (the request; the `change_request_status` observation of a named run; a run-level artifact such as a confirmation metrics report) or "unknown"; the cost of the search: the sum of cost totals over the key's runs since the previous accepted change, failed and cancelled included, tokens first, USD beside, with completeness | the target view's runs, their approval requests and cost totals |
| target page: history | the key's runs newest first in the graph page's row style, each with its cost | the target view |

## Approvals

| element | content | route |
|---|---|---|
| waiting | rows: target, graph, how long it waits and the deadline, the bound commit, `run_cost`; click opens the review page | `GET /namespaces/{ns}/approvals?status=pending`; the deadline from the node's wait (ADR-0015) |
| history | rows: target, decision and approver, time, commit; `invalidated` and `expired` with their reason | `GET /namespaces/{ns}/approvals?status=` (ADR-0015) |

## Cost

| element | content | route |
|---|---|---|
| today | the namespace's spend today against `daily_budget_usd` as a bar; "admission blocked" when the daily view says so | `GET /namespaces/{ns}/cost/daily` |
| by graph | the graphs with their tokens today, linking to the graph pages | the graphs list summary (ADR-0015) |

Daily history and monthly totals are after MVP.

## System

Read-only in MVP; one health line on top from `GET /readyz`.

| block | content | route |
|---|---|---|
| Workers | id, runtime, version, capacity, `last_seen_at`; highlighted when silent longer than the lease period | `GET /workers` |
| Schedules | per graph: trigger kind, spec, `next_fire_at`, paused | `GET …/graphs/{graph}/triggers` |
| Presets and artifact types | the lists; a body on click | `GET /namespaces/{ns}/presets`, `GET /namespaces/{ns}/artifact-types/{name}` |
| Policy | the namespace policy as a read-only form | `GET /namespaces/{ns}` |

## Attempt comparison

Opens from the node panel; two attempts of one node, the counterpart as the route defaults it.

| section | left and right | route |
|---|---|---|
| outcome | status and error class; `strategy` and `strategy_fallback`; the `Feedback` passed in with author and association | `GET …/attempts/{att}/diff?against=` |
| outputs | per declared output: structured outputs as the changed fields from the JSON Patch; `VcsRef` as branch, commit, diff statistics, change-request link | the same |
| cost and events | usage per model with completeness; event counts | the same |
| configuration and versions | changed config paths from the resolved config digests; `harness_version`, `image_digest`, `price_table_id` | the same |

Identical rows are muted.

## Logs

Opens from the node panel; one attempt.

| element | content | route |
|---|---|---|
| transcript | the normalised events in order: `turn` and agent messages as text in the serif typeface; `tool_call` and `tool_result` as one collapsible row (tool, arguments in brief, duration, outcome) expanding to the full arguments and result, truncation markers kept; `permission_denied` and `usage` as rows of their own colour; `result` and `error` at the bottom | `GET …/attempts/{att}/logs?follow=true`, paged at 1,000 events |
| live tail | follows while the attempt runs; pauses when the reader scrolls up; a "follow" control resumes | |
| raw transcript | a download link | `GET …/attempts/{att}/logs?raw=true` |
| no access | without the `transcripts` capability the page states that access is missing | `API005` |
