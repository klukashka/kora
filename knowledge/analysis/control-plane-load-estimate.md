# Control-plane load estimate and the choice of Python

Date: 2026-10-16. Method: back-of-the-envelope estimate from the scale targets in NFR-1.1 and the protocol and API design in `knowledge/specs/`; nothing was measured. The load test listed in [docs/roadmap.md](../../docs/roadmap.md) replaces these numbers once code exists.

## Question

At the target scale, does Python become the bottleneck of any kora component, and where would Go or Rust be materially better?

## Assumptions

- 500 agent sessions running concurrently at peak, about 150 on average (NFR-1.1: hundreds of concurrent agent nodes).
- 50,000 runs per month with about 20 node instances each, so about one million node instances per month.
- A session lasts about 20 minutes and produces about 300 normalised events (turns, tool calls, tool results): about 0.25 events per second per session, 2 KB per event on average.
- Workers upload session events in segments every 5 seconds and renew leases every 15 seconds ([ADR-0003](../../docs/decisions/0003-scheduler-and-workers-over-http.md)); workers poll for claims once per second.
- Tens of people and their own coding agents use the console, the CLI and the MCP servers at the same time.

## Estimated load at peak

| component | load | capacity of the chosen stack | headroom |
|---|---|---|---|
| API: heartbeats | 500 / 15 s ≈ 33 requests/s | | |
| API: segment uploads | 500 / 5 s ≈ 100 requests/s | | |
| API: claims, state transitions, webhooks | ≈ 25 requests/s | | |
| API: console, CLI, MCP | ≈ 10–50 requests/s | | |
| API total | ≈ 200 requests/s, bursts to ≈ 500 | FastAPI with async SQLAlchemy: roughly 500–1,500 database-backed requests/s per process | 3–10× on one process, then horizontal |
| PostgreSQL writes | ≈ 150/s | thousands per second on modest hardware | more than 10× |
| Scheduler | tens of changed rows per pass, one pass per second; about one node start per second on average | the work is SQL, not Python | very large |
| Worker event relay | ≈ 125 events/s, peak ≈ 600 | JSON parsing at 10–50 µs per event is 1–3 % of one core | very large |
| Session runner | ≈ 0.25 events/s per session | negligible | |
| CEL evaluation | single-digit evaluations per second | Rust-backed binding ([ADR-0013](../../docs/decisions/0013-implementation-stack.md)) | not a concern |

## Findings

- **CPU is not the constraint.** kora's throughput is three to four orders of magnitude below the level where the implementation language decides; Temporal is written in Go because it runs millions of workflow steps per second, while a kora unit of work is a session lasting minutes to hours. Python orchestrators (Airflow, Prefect, Dagster) run far larger task volumes.
- **Storage volume is.** 125 events/s at 2 KB is about 20 GB per day at peak concurrency and about 6 GB per day at average concurrency; with the 30-day retention of FR-4.5 that is about 200 GB of segments and transcripts. Local-disk artifact storage does not fit an organisation deployment, which is why S3-compatible storage is in MVP (FR-4.4).
- **Event-loop blocking by large responses** (a large run page or artifact) is the realistic Python-specific risk. Several Uvicorn processes and a fast JSON encoder are the standard mitigation.

## Where Go or Rust would be better

| place | better in Go or Rust | why the stack stays |
|---|---|---|
| Session runner, one process per session | memory: about 50–80 MB per Python process against about 10 MB for Go, so 25–35 GB against 5 GB at 500 sessions | the harness itself (Claude Code on Node.js) takes 300 MB or more per session, so the saving is 10–15 % of a session's memory; the Claude Agent SDK exists for Python and TypeScript only and the Codex SDK for TypeScript only, so a Go runner would lose the Claude SDK and return to parsing CLI output, which [ADR-0004](../../docs/decisions/0004-harness-adapters-over-vendor-sdks.md) rejected |
| CLI | start-up: instant against 0.3–0.5 s of imports | noticeable but tolerable; lazy imports reduce it |
| Worker inside an adopter's network | one static binary is easier to distribute | the Python runner has to be in the runtime image next to the harness anyway |
| API and scheduler | no, at this scale | the database and the model providers are the limits |
| CEL | already Rust through the binding | |

## Recommendation

Keep the stack. The scale target is reached by running more API processes and workers and by batching segments, not by changing language. The session runner is the only candidate for a rewrite, and only if runner memory becomes a measured problem in the pilot and an agent SDK exists in the target language (TypeScript today).
