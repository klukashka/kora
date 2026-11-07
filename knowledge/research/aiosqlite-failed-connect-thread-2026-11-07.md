# aiosqlite worker thread after a failed SQLite connect

Date: 2026-11-07. Implementing issue: [#918](https://github.com/realkarych/kora/issues/918). Governing contract: [scheduler section 10](../specs/scheduler.md#10-standalone-sqlite-versus-postgres), the SQLite "connection" row. Versions checked: aiosqlite 0.22.1, the latest release on this date, and SQLAlchemy 2.1.5.

## Failures

| Run | Lane | Head | Result |
|---|---|---|---|
| [42357463000](https://github.com/realkarych/kora/actions/runs/42357463000/job/128359325877) | Ubuntu 24.04, Python 3.15.0, measurement cohort | `cf3d59d5` | 1228 passed, 576 deselected, 1 error |
| [42366812306](https://github.com/realkarych/kora/actions/runs/42366812306/job/128386512369) | Ubuntu 24.04, Python 3.11.17, measurement cohort | `35cbc46b` | 1228 passed, 576 deselected, 1 error |

In both runs, the error is a `PytestUnhandledThreadExceptionWarning` reported at the setup of `test_real_database_connection_unavailability_is_not_bad_credentials[postgresql]`, right after the `[sqlite]` case of the same test passed. In `measurements.log`, a `_connection_worker_thread` raised `RuntimeError: Event loop is closed` at `aiosqlite/core.py:66`, where the worker calls `call_soon_threadsafe(set_result, ...)`. While it handled that error, the worker raised again at `core.py:75` (`set_exception`). Neither pull request touched the database layer.

## Cause

The thread is started by a failed `aiosqlite.connect`:

1. `Connection.__await__` starts the worker thread, and `_connect` queues the `sqlite3.connect` call on it.
2. When that call raises, `_connect` calls `stop()` and re-raises. `stop()` queues a close step with a future, and `_connect` never awaits that future.
3. The worker then runs the close step on its own. The step succeeds, so the worker reports its result with `call_soon_threadsafe`. If the loop has closed by then, that call raises at `core.py:66`, and reporting that error raises again at `core.py:75`.

The `core.py:66` frame shows that a queued step succeeded. A failed open cannot succeed, so the step was this close step. The `[sqlite]` case makes the database path a directory, so its `authenticate` (reader pool) and `revoke` (writer pool) calls each fail once to connect. Each failure leaves one such worker.

Two other explanations were excluded:

- **A pooled connection.** `Database` opens no connection when it is constructed, because both pools connect lazily.
- **`Database.close()`.** For a connection that did open, `aiosqlite.Connection.close()` awaits its stop future.

The defect is reported upstream as [omnilib/aiosqlite#394](https://github.com/omnilib/aiosqlite/issues/394), open on this date, and upstream main has the same `_connect`.

## Reproduction

All probes ran on arm64 macOS against `master` at `38d18e72`, with real aiosqlite, SQLAlchemy and `kora.db.connection` and no test doubles. Every probe counts live threads whose name ends in `(_connection_worker_thread)`. A failure is an SQLite path replaced by a directory.

| Probe | Python 3.11.17 | Python 3.15.0 |
|---|---|---|
| `await aiosqlite.connect(directory)` in a fresh loop: a worker is alive when the call raises | 2000 of 2000 | 2000 of 2000 |
| The same, with the loop closed right after the call: a worker raised `RuntimeError: Event loop is closed` | 1 of 2000 | 1 of 2000 |
| `Database.reader_engine.connect()` and `writer_engine.connect()`: a worker is alive when the call raises | 1000 of 1000 | 1000 of 1000 |
| The test's own path through `Database.read`, `Database.run` and `Database.close()`: a worker is alive after `close()` returns | 0 of 2000 | 0 of 2000 |
| The same path, with a `threading.setprofile` hook holding each worker's close step until the loop had closed | 2 workers alive after `close()`, then 2 errors with the `core.py:66` and `core.py:75` chain | Not run: the hook crashes Python 3.15.0 when greenlet is loaded |

The defect is therefore not specific to Python 3.15. Through `Database`, a worker normally finishes within the few loop turns before `close()` returns, so a CI failure needs a worker that is delayed long enough for the test's loop to close first. A loaded runner fits that pattern, but neither run shows why the worker was late.

Two neighbouring paths were checked and leave no worker:

- **A file that is not a database.** It opens, then fails in the `connect` event pragmas. SQLAlchemy 2.1.5 closes that connection gracefully, which awaits the worker.
- **The other SQLite tests.** No other test in the repository makes an SQLite file fail to open. Closed-database tests raise `DatabaseClosedError` before connecting.

## Fix and its evidence

`kora.db.connection` passes the SQLite engines an `async_creator`. The creator opens the file with `sqlite3.connect` through `asyncio.to_thread`, using the absolute path and today's arguments. Only after a successful open does it wrap the connection in an `aiosqlite.Connection` subclass whose thread is a daemon. A failed open therefore starts no worker.

With the fix, the probes above report no live worker and no thread error on either Python version. `tests/db/test_driver_threads.py` holds the regression tests. On the code before the fix, its `[sqlite]` failure case fails at the check made when `engine.connect()` raises, because the worker exists at that moment. The check after `Database.close()` passes on both versions, as the table shows.

## Reading rule for thread counts

The idle default-executor worker started by the first open stays until the event loop shuts its executor down. Two lifetime measurements count threads once per cycle inside one event loop: the metadata close scenarios through `threads_returned_to`, and the pricing release cycles through `threading.active_count()`. They pass because an earlier step on the same loop has already started that worker: migrations in the metadata measurement, and warm-ups in the pricing measurement. If those steps are reordered, a single extra thread in the first cycle is this executor worker, not a leaked connection.

## Known remaining path

A forced terminate in SQLAlchemy calls aiosqlite `stop()` without awaiting it, which is the same upstream defect. A graceful terminate does await it. A direct `Database.read_transaction()` cancelled twice reached the forced path, and the fix does not change that path. Remove the creator once an aiosqlite release awaits `stop()` in `_connect`, and repeat the probes above when that happens.
