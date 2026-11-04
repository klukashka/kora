# macOS 26 CI qualification candidate

Checked on 2026-11-04 for issue #720. This record describes a candidate environment;
no hosted macOS 26 execution or qualification is claimed.

## Evidence and scope

The [GitHub hosted-runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
lists `macos-26` as a standard arm64 image. Selecting it changes the OS image and
preinstalled toolchain. It does not establish a separate capacity pool or guarantee
runner allocation. The locked Python versions and dependencies remain unchanged.

The queue pilot #717 failed to acquire two hosted macOS runners. PR #715 attempt 2
lost storage and remainder before their first steps. Those failures remain failures;
a different environment does not retroactively qualify their evidence.

The candidate changes all six full-CI macOS executions and their aggregate status
to `macos-26`. Its twelve-lane verifier requires the actual new environment identity
and unchanged run, attempt, revision, coverage and integrity bindings. It rejects
old macOS receipts, including old receipts placed under new directory names.
Linux jobs, test inventories, fixture preparation and cleanup, permissions, pinned
actions and dependencies, and hard deadlines are unchanged. The separate storage
release workflow remains on `macos-15`; historical measurements retain their
original environment identities. No macOS 15 equivalence is claimed.

## Required-status migration proposal

Read-only inspection of the master branch API reports fifteen required check
contexts. One names `Scaffold (macos-15, Python 3.11.17)` with GitHub Actions app ID
15368. The candidate emits `Scaffold (macos-26, Python 3.11.17)` instead. The active
repository ruleset separately requires linear history and CodeQL scanning.
The full legacy protection endpoint returned HTTP 404; the branch endpoint supplied
the check list. This is not proof that all protection settings are visible.

Before an accepted rollout can merge, the owner must approve replacing only that
one required check identity, retaining app ID 15368 and all other protection
settings. No protection mutation was performed. Do not remove the old requirement
before the candidate has passed its complete native qualification. Re-read the
full available protection state immediately before any separately authorized
migration; do not reconstruct unknown settings from this partial snapshot.

## Qualification and stopping rule

Local routing, receipt integrity and static checks validate configuration, not
native compatibility or capacity. Independently review correctness, security and
resource/budget boundaries before publication. After the already-running #719 CI
settles, separately authorize one candidate publication and full Linux/macOS run.
Require actual macOS setup, all test lanes, host fixtures and cleanup, twelve
same-attempt receipts and successful aggregate checks. Record total time including
scheduling separately from hard deadlines. A failed or incomplete candidate stops
the experiment; no unchanged retry campaign or cross-attempt evidence admission.

Native macOS 26 compatibility, runner availability and final-head CI are currently
unverified. The feature PRs require their own successful integrated revisions after
any accepted environment change; this candidate does not make them merge-ready.
