# Conservative estimate for unrecorded test modules, 2026-11-20

Checked on 2026-11-20 for [issue #1690](https://github.com/realkarych/kora/issues/1690). It follows the [record of 2026-11-19](linux-lane-durations-2026-11-19.md) for issue #1688. The numbers are observations of hosted runners. They are not a portable benchmark and set no numerical gate.

## What failed

After #1689 the committed duration table came from run 44585251370 at `c45419ff`. The `master` push runs [44626340182](https://github.com/realkarych/kora/actions/runs/44626340182) at `f3436790` (#1524) and [44645927946](https://github.com/realkarych/kora/actions/runs/44645927946) at `1dfaed20` (#1483) then had Ubuntu 3.11 `general-3`, and Ubuntu 3.11 `general-5` and Ubuntu 3.15 `general-1`, cancelled at the ten-minute job deadline.

## Cause

The planner estimated a test of a module absent from the table at the environment's mean recorded time per test. The worker API modules added by #1518, #1524 and #1483 were absent:

| Run, environment | Unrecorded modules | Tests with JUnit time | Recorded | Per test |
|---|---|---|---|---|
| `f3436790`, Ubuntu 3.11 | 23 | 298 | 1,432 s | 4.8 s |
| `f3436790`, Ubuntu 3.15 | 20 | 296 | 1,327 s | 4.5 s |
| `1dfaed20`, Ubuntu 3.11 | 47 | 387 of 519 | 1,363 s | 3.5 s |
| `1dfaed20`, Ubuntu 3.15 | 44 | 440 of 512 | 1,419 s | 3.2 s |

Tests of the cancelled lanes have no JUnit time. The largest were `tests/api/test_worker_history_authority.py` (223–282 s for 48 tests), `tests/engine/test_worker_claim_poll.py` (114–227 s for 30 tests) and `tests/api/test_worker_history_query.py` (155–180 s for 40 tests).

Per-test rates of the recorded modules in the committed table, one value per module:

| Environment | Mean per test | 75th percentile | 90th percentile | 95th percentile | Maximum |
|---|---|---|---|---|---|
| Ubuntu 3.11.17 | 0.39 s | 1.1 s | 4.3 s | 6.5 s | 28.6 s |
| Ubuntu 3.15.0 | 0.32 s | 1.0 s | 3.3 s | 5.0 s | 14.2 s |

The mean is weighted by test count and dominated by many fast tests, so it sits far below a typical new integration module. The 90th percentile is close to the measured worker modules. The maximum would plan a new module of a hundred fast tests at about half an hour.

## Planner replay

The existing `lane_count` and `assign` functions were run on each Ubuntu collection of those runs with the committed table, the unrecorded-module estimate replaced in turn, and a maximum high enough not to bind. The largest child is measured from JUnit where present and estimated elsewhere.

| Collection | Mean | 75th percentile | 90th percentile | 95th percentile |
|---|---|---|---|---|
| `f3436790`, Ubuntu 3.11 | 10 lanes, 539 s | 11, 568 s | 12, 379 s | 13, 346 s |
| `f3436790`, Ubuntu 3.15 | 9, 408 s | 9, 440 s | 10, 378 s | 10, 375 s |
| `1dfaed20`, Ubuntu 3.11 | 10, 463 s | 11, 431 s | 13, 407 s | 14, 343 s |
| `1dfaed20`, Ubuntu 3.15 | 9, 488 s | 9, 574 s | 11, 400 s | 12, 316 s |

With durations measured in the same runs merged into the table, Ubuntu 3.11 needs 11–12 lanes, 8,700–9,600 s of estimated time against the 9,720 s that twelve lanes of three children hold at the 270-second target. A conservative estimate alone would therefore make the planning job refuse the `1dfaed20` collection at a twelve-lane maximum.

## Decision

On 2026-11-20 co-owner `klukashka` approved:

- the nearest-rank 90th percentile of the environment's recorded per-test module rates as the estimate for a test of an unrecorded module;
- a lane maximum of sixteen per environment.

The table, its refresh procedure, the 270-second target, three children per lane, the ten-minute deadline, the macOS limit of two parallel scaffold jobs and the test inventory are unchanged.

## Limits and revisit triggers

- An unrecorded module of fast tests is overestimated until the next refresh and may take more lanes than it needs, within sixteen.
- The general collection grew by about 1,000–1,500 s with each of the three worker API pull requests, whose tests run 3–8 s each. Sixteen lanes hold 12,960 s. Revisit trigger: a planning refusal or a lane count above fourteen on `master`.
- A collection-level estimate still misses a new module that is slower than the 90th percentile, as `tests/api/test_worker_history_diagnostics.py` was at 30 s per test on Ubuntu 3.15.
- More Linux lane jobs per run queue longer when the account's concurrent job limit is reached. This is a diagnostic against the ten-minute feedback target.
