# The web console

Status: Reviewed by the project owner on 2026-10-16. This page records the design of `kora web` (FR-7.3, NFR-13.1, requirements decision 21), settled before any console code exists; it is roadmap step 2. The element-by-element detail of every page, the mapping of every internal state to what a person sees, and the two pilot scenarios are in [knowledge/design/console/](../knowledge/design/console/pages.md). The decisions that change the API or the console's scope are recorded in [ADR-0015](decisions/0015-web-console-design.md). Facts about other orchestrators' consoles that informed the design are dated in [knowledge/research/orchestrator-consoles.md](../knowledge/research/orchestrator-consoles.md).

## Who it is for

The console is designed for the engineer who owns targets: the person who triggers a run, waits for it, approves the change it produces and handles the errors on the way. Those are the three human touchpoints of the canonical use case in [positioning.md](positioning.md), and they are the three things the console lets a person do. The platform team has its own section, System, which is never the entry screen.

The guiding picture is the one an Airflow operator already has: the engineer looks at the whole graph, and the nodes are representative by themselves of what finished, what failed and what waits for a reaction. The console is not an inbox of alerts; the graph is the primary view, and the lists around it exist to reach a graph and a run quickly.

## The entry: the graphs of a namespace

A top bar holds the namespace switcher on the left, the five sections Graphs, Targets, Approvals, Cost and System, and the user on the right; the page below gets the full width. The list is one namespace's; the last chosen namespace is remembered in the browser, and the first entry lands on the first namespace of the token.

The entry is the list of the namespace's graphs, like the DAG list in Airflow. A graph card answers two questions, what this graph is and whether to jump into it, with exactly five elements:

- the graph's name and its one-line description from the graph document;
- the number of declared nodes of the latest version (a fan-out and a sub-graph count as one node each);
- a traffic light over the graph's active runs, counting only stop states: runs waiting for a person, runs that failed, runs that are running; zeros are hidden; a routine failed sub-graph attempt is not red, and a run in `failed` counts as failed until it is retried or cancelled;
- the tokens spent today by the graph's attempts (the window of the namespace's daily soft limit), with the USD estimate beside it when the billing class allows one.

Triggers and their pause state, the last run, the version and its deploy date are not on the card: none of them changes the decision to jump in. They are on the graph page. A namespace with one graph opens that graph's page directly.

## The graph page

The page shows one run of the graph at a time. On the left, the runs of the graph, newest first, one row per run: a strip of the top-level nodes' states, the target key, when the run started and what triggered it; the newest run is drawn by default, as in Airflow. Above the graph, one line of facts about the selected run: the target, the state as a word and how long it has run, who or what triggered it, tokens and USD so far, the graph version, links to the run it re-runs and to its reruns, and the Run button that opens the trigger form. Below the graph, the event timeline collapsed to one line, expanding to the list of events with a filter by node; a clicked event highlights its node.

The graph is the pinned version's topology, laid out once and never moved by state changes. Every node is a rectangle of the same size:

- zoomed out, a node shows only the icon of its kind (agent, command, branch, fan-out, sub-graph, human input, event wait, timer), drawn in one hand-drawn style; the rectangle's colour is the node's state;
- zoomed in, the icon moves to a corner and the node shows its name and one line that states time by state: running "48 min", done "in 38 min", failed with the error class in one word, waiting with what it waits for and how long, nothing when not started;
- a node whose declared definition runs several times in parallel (the body of a fan-out) is one rectangle coloured by the worst state of its instances, with a strip inside it holding one segment per instance;
- a fan-out is collapsed into one node with the per-element strip and a summary such as "4 hypotheses: 1 accepted · 1 failed · 2 running"; it expands in place into a frame holding the sub-graph body drawn once, every body node carrying the strip. Only the current sub-graph attempt of each element is drawn; earlier attempts and their rejection reasons are listed in the panel.

A click on a node opens a side panel in a fixed order: the state and its reason with the error text; the attempts, the current one expanded (harness, model, strategy and fallback, time, tokens, USD) and earlier ones collapsed to a line; the artifacts of the current attempt; links to the logs and to the attempt comparison. A human-input node carries a button to the review page on top; a failed node carries the retry button.

## States, colours and words

Five colours carry every state: gray for not started, ready, skipped, upstream-failed and cancelled (the last three with a mark); blue for running; amber for waiting; green for succeeded; red for failed. Amber means the person's move only: a node waiting for an approval. A timer, an event, a sensor poll, a retry back-off, the queue for a worker and an admission wait (a concurrency limit, the daily budget, a held target) are blue with the reason in the node's second line. Colour answers whose move it is: blue the system's or the outside world's, amber the person's, red stopped, green done, gray not yet. Error classes never colour a node; they are words in the second line.

The console names states with the API's words (`running`, `succeeded`, `failed`, `cancelled`, `skipped`, `pending`), the same words the CLI prints and the MCP tools return, so that a person, a script and an agent describe one run in one vocabulary. The one phrase the console adds is `waiting for you` for the amber group. A run is red when failed, green when succeeded, gray when cancelled, amber when the only live work in it waits for a person, otherwise blue. The console's language is English.

## The review page

The review page is one approval request ([ADR-0008](decisions/0008-content-bound-approvals.md)). It opens from the human-input node and from Approvals, and it reads top to bottom:

1. one line stating what is being approved: the commit, the branch, the link to the change request, the target;
2. the facts kora wrote: the metrics as one table from baseline to candidate with the gain, every value followed by a small word of origin ("measured · dev_run", "reported by the agent · review"); the gates with their verification result ("verified" against the protected paths or "unprotected"); the run's cost with its completeness; the changed paths and the diff statistics, with the diff itself linked on the code host (kora stores no diff);
3. the block the agent wrote, labelled as such: the summary and the rationale, set in the serif typeface;
4. the form rendered from the request's JSON Schema and a button that names the bound commit.

When the branch moved during review, the page reloads and shows a banner with the bound commit and the observed head, states that nothing can be approved on this evidence, removes the form, keeps the facts and the narrative readable as history, and offers the one action that changes the situation: retry the producing node with feedback, with a link to that node in the graph. Without the `approve` flag the button is neither hidden nor disabled; pressing it explains the missing right.

## The target page

The target page is the projection of [ADR-0014](decisions/0014-target-identity.md), in three blocks: now (the run holding the target and since when, what it does, with the link to the review page when it waits for a person; the runs deferred behind it); accepted (the latest approved change with its commit, link, time and approver; a strip of three facts kept apart, accepted, merged and verified after rollout, each with its source and "unknown" where no node of the graph reports it; the cost of the whole search for that change, the sum over every run of the key since the previous accepted change, failed and cancelled runs included); and the history, the key's runs newest first in the same row style as on the graph page, with each run's cost.

The Targets list shows active targets by default: the key, the strip of the holding run, the accepted change and the number of deferred runs.

## Approvals, Cost and System

Approvals has two blocks: the requests waiting for a person (target, graph, how long and the deadline, the bound commit, the run's cost), each opening the review page, and the history of responses (decision and approver, time, commit; invalidated and expired requests with their reason).

Cost shows the namespace's spend today against its daily soft limit, the admission-blocked flag when it is set, and the graphs with their tokens today. Daily history and monthly totals follow after MVP.

System is the platform team's read-only section: workers (name, runtime, version, capacity, last seen, highlighted when silent longer than a lease), schedules (graph, trigger, next fire, paused), presets and artifact types, the namespace policy as a read-only form, and one health line. Writes stay in the CLI and the MCP tools with the `platform` flag.

## Attempt comparison and logs

The attempt comparison page shows two attempts of one node as two columns by fixed sections: the outcome (status, reason, strategy and fallback, the feedback passed in and from whom), the outputs (changed fields of structured outputs, both branches and commits with diff statistics and code-host links), cost and event counts, configuration and versions; identical rows are muted. The logs page is the transcript of an attempt: the agent's messages as text in the serif typeface, every tool call as a collapsible row with the tool, the arguments in brief, the duration and the outcome, permission denials and usage as rows of their own colour, a live tail that pauses when the reader scrolls up, and the raw transcript as a download.

## Actions

The console performs the three human touchpoints and nothing else: approve, on the review page; run a graph with parameters, from a form rendered from the graph's parameter schema on the graph page, always on the latest active version; retry a failed node, from its panel and from the stale-revision banner, through a dialog that states the strategy taken from the node's policy and lists the nodes that the retry will reset; cancel a run or a node, with a confirmation that names what stops. Every action is the same API operation the CLI and the MCP tools call ([ADR-0002](decisions/0002-ai-first-api-and-thin-clients.md)). Graph deployment and every platform write stay in the CLI and the MCP tools.

## Visual language

- A sans-serif typeface for the console and its facts; a serif typeface for everything a model wrote: the bundle's narrative, the agent's messages in logs, hypothesis texts, review-session comments. Typography shows who is speaking before a label does, which is how facts and narrative stay apart on every screen.
- Eight node-kind icons drawn for kora in one hand-drawn stroke; interface icons from the Lucide set that shadcn/ui ships.
- Light and dark themes from the first day, following the system setting with a switch in the user menu; the five state colours are defined as pairs and checked in both; the design review of NFR-13.1 covers every screen in both themes.
- Quiet transitions: a colour changes with a short transition and nothing pulses, spins or blinks; running is shown by the colour and a time line refreshed once a minute; the graph layout never moves with states; new rows appear at the top of lists without shifting the scroll position.
- One primary task per screen: the graph page shows a graph, the review page lets a person decide, the target page says where a target stands; detail opens on click, never in a dense panel.

## What the console needs from the API

The console is a composition of public routes and adds no route of its own. The design found data the routes did not yet expose; [ADR-0015](decisions/0015-web-console-design.md) records what they expose now: a per-graph summary on the graphs list, the node's current wait, the attempt's worker and lease, a status filter on the approvals list, and the collection phase after a session ends. The two pilot scenarios in [knowledge/design/console/pilot-scenarios.md](../knowledge/design/console/pilot-scenarios.md) walk through the console step by step and name, for each step, the view and the route that answer it.
