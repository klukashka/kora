# Linux general lanes at the job deadline, 2026-11-19

Checked on 2026-11-19 for [issue #1688](https://github.com/realkarych/kora/issues/1688). It follows the [lane-duration record of 2026-11-12](scaffold-lane-durations-2026-11-12.md). The numbers are observations of hosted runners. They are not a portable benchmark and set no numerical gate.

## What failed

After #1680 (`e7cd3570`), one Linux Python 3.11.17 general lane reached the ten-minute job deadline in the `master` push runs at `e7cd3570` (`general-7`), `964433ec` (`general-5`) and `c45419ff` (`general-8`), and in attempt 2 of run 44573833758 of pull request #1687 (`general-8`). No test failed in those lanes. The evidence gates then failed on the missing lane.

## Cause

`scripts/ci/lane_durations.json` came from run 43627734292 at `a8174fe5`. The planner estimates a test of an unrecorded module at the environment's mean recorded time per test, 0.28 s on Ubuntu 3.11. In the green `master` push run [44532886745](https://github.com/realkarych/kora/actions/runs/44532886745) at `b06c0ae0`, 87 modules absent from the table took 1,417 s on Ubuntu 3.11 against an estimate of 623 s. The slowest took 3–9 s per test, about equally on SQLite and PostgreSQL:

| Module | Tests | Recorded | Estimate |
|---|---|---|---|
| `tests/engine/test_admission_preparation_observation.py` | 12 | 112 s | 3 s |
| `tests/engine/test_attempt_admission.py` | 36 | 105 s | 10 s |
| `tests/engine/test_attempt_admission_boundaries.py` | 22 | 91 s | 6 s |
| `tests/engine/test_optional_input_concurrency.py` | 14 | 84 s | 4 s |
| `tests/engine/test_admission_processing_lifecycle.py` | 10 | 82 s | 3 s |

Recorded modules also grew, for example `tests/cli/test_conformance.py` from an estimate of 204 s to 289 s.

## Planner replay

The existing `lane_count` and `assign` functions were run on each Ubuntu environment's complete general collection of that run, with the committed table and with a table built from that run's JUnit times by `environment_durations`. The replay reproduced the run's largest actual child, 441 s on Ubuntu 3.11.

| Environment | Recorded total | Committed table: lanes, largest child estimate, largest child recorded | Table from the run: lanes, largest child |
|---|---|---|---|
| Ubuntu 3.11.17 | 7,200 s | 8, 249 s, 441 s | 9, 267 s |
| Ubuntu 3.15.0 | 6,555 s | 8, 242 s, 524 s | 9, 243 s |
| macOS 15 3.11.17, scheduled run 44504923700 | 3,659 s | 5, 223 s, 395 s | 5, 244 s |

Eight lanes of three children hold 6,480 s at the 270-second target, so with current durations the planning job refuses Ubuntu 3.11 at an eight-lane maximum. The 2026-11-12 record gives no reason for eight beyond the collection then needing four lanes.

## Decision

On 2026-11-19 co-owner `klukashka` approved raising the lane maximum to ten per environment and refreshing the table through the documented procedure. The 270-second target, three children per lane, the ten-minute deadline, the macOS limit of two parallel scaffold jobs and the test inventory are unchanged.

## Limits and revisit triggers

- The unrecorded-module estimate stays the mean. A batch of heavy new modules between refreshes can overload a lane again. Revisit trigger: the evidence job's over-plan notice or a lane's 450-second warning on a `master` run.
- Ten lanes of three children hold 8,100 s, about 12% above the current Ubuntu 3.11 total.
- Each Ubuntu environment gains one lane job now, which queues longer when the account's concurrent job limit is reached. This is a diagnostic against the ten-minute feedback target.
- A replay uses one run's runner speed; a refreshed table carries the same limitation.
