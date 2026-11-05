# Recovery retirement failure evidence

Checked on 2026-11-05 for [issue #738](https://github.com/realkarych/kora/issues/738),
a diagnostic child of [#460](https://github.com/realkarych/kora/issues/460).

## Observed gap

The local integration of PR #715 with master `76e8087a` failed
`test_starting_entry_resolves[incomplete_listing_is_unconfirmed]` at its second
retirement. The first retirement intentionally refused because the configured
scan capacity was one entry. The reopened runtime used its normal capacity, but
returned `runner_stop_unconfirmed`. The retained traceback did not include its
retirement outcome or scan subreason. Concurrent process churn was a hypothesis,
not an established cause.

The failing integration tree was `422b8b0d3575866427d0157417f5e1817c7c7577`.
Its local suite reported one failure, 628 passes and ten existing host/measurement
deselections. Separately, the hosted PR attempt had macOS jobs abandoned before
execution because hosted runners were unavailable. These observations do not
establish one common cause.

## Bounded attribution

The existing `retirement_failure_note` helper retains its single-call behavior.
The recovery scenario opts into a stricter selector over its existing bounded
capture. That selector requires exactly two retirement events. The first must
pass the existing safe-field validator and describe `listing_incomplete` with
`pid_listing_incomplete`, zero matched carriers and zero delivered signals. Only
the second event is then validated and projected into the exception note.

Missing, extra or invalid retirement events produce `null`; the selector never
falls back to the first event. Unrelated event types do not affect selection.
This attribution relies on the isolated capture and sequential two-call test;
it is not a general correlation mechanism for concurrent retirements.

The note is attached only after the recovery runtime closes. Capture failure
preserves the original protocol error and records the capture failure as its
cause. Successful recovery retains the existing event assertions. Outer runner
cleanup, fence/receipt assertions, scan capacity and every deadline are unchanged.
The allowlisted projection excludes paths, nonce, argv, exception text and unknown
fields. Diagnostic events do not authorize termination or establish disappearance.

## Verification scope and limits

The ordinary real-runtime note regression also exercises two distinct refusals:
the configured scan-capacity refusal followed by `match_unreadable` through the
existing configured identity reader. It verifies original exception identity,
the second outcome, the fenced live carrier before cleanup, executor release and
final process/descriptor cleanup. A one-byte capture variant exercises overflow
without replacing the original failure.

Parser regressions cover absent, earlier-only, extra, reversed and invalid
sequences, unrelated events and private-field canaries in both records. These
checks establish diagnostic behavior, not the cause of the historical refusal.
The original recovery workload remains mandatory on Linux and macOS. No runtime
repair, retry campaign, relaxed assertion or extended deadline is part of this
change. If a future failure provides attributable evidence, inspect the demonstrated
owning boundary before proposing a production fix.
