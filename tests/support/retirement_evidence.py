import json
from collections.abc import Generator
from contextlib import contextmanager

from kora.conformance.capture import Capture, CaptureOverflow, CaptureTimeout
from kora.protocols.errors import KoraProtocolError
from tests.measurements.test_local_runtime_cost import last_retirement_observation


@contextmanager
def retirement_failure_note(
    capture: Capture, *, after_incomplete_listing: bool = False
) -> Generator[None]:
    try:
        yield
    except KoraProtocolError as error:
        if error.code != "runner_stop_unconfirmed":
            raise
        try:
            records = capture.finish()
        except (CaptureTimeout, CaptureOverflow, json.JSONDecodeError) as failure:
            error.add_note("last observed runtime.local.retire: null (unverified)")
            raise error from failure
        error.add_note(
            json.dumps(
                {
                    "last_observed_runtime.local.retire": (
                        recovery_retirement_observation(records)
                        if after_incomplete_listing
                        else last_retirement_observation(records)
                    )
                },
                sort_keys=True,
            )
        )
        raise


def recovery_retirement_observation(
    records: list[dict[str, object]],
) -> dict[str, object] | None:
    retirements = [
        record for record in records if record.get("event") == "runtime.local.retire"
    ]
    if len(retirements) != 2:
        return None
    initial = last_retirement_observation(retirements[:1])
    if (
        initial is None
        or initial["outcome"] != "listing_incomplete"
        or initial["scan_failure"] != "pid_listing_incomplete"
        or initial["matched"] != 0
        or initial["killed"] != 0
    ):
        return None
    return last_retirement_observation(retirements[1:])
