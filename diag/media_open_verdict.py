import argparse
import json
import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from diag import media_open_schedule
from diag.media_open_records import load, valid

QUALIFYING = {"H1", "H2", "H3", "H4"}
RESIDUAL_LIMIT = 1
QUALIFYING_MINIMUM = 760
O_REFUSAL_MINIMUM = 3
CLEARED_MINIMUM = 5
PERSISTED_LIMIT = 0.02
PERSISTED_DECISIVE = 9
DEADLINE_MARGIN = 2
LOCAL_REST = 50
LOCAL_HOLDER = 50
LOCAL_FIRST = 10


class ReaderCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    absent_disk: dict[str, str]
    slice_name: dict[str, str]
    slice_iokit: dict[str, str]
    detached_media: dict[str, str]
    handles_balanced: str


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal[
        "complete",
        "missing",
        "interrupted",
        "stopped_leftovers",
        "stopped_control_child",
        "stopped_local_first_rest",
    ]
    cycles_recorded: int
    mode: Literal["hosted", "local"]
    host: str
    macos: str
    image: str

    @model_validator(mode="after")
    def schema_values(self) -> Self:
        for name, value in (
            ("host", self.host),
            ("macos", self.macos),
            ("image", self.image),
        ):
            if not valid(name, value):
                raise ValueError("run summary outside the fixed schema: " + name)
        return self


def summary(directory: Path, rows: list[dict[str, str]]) -> RunSummary:
    path = directory / "run.json"
    if path.is_file():
        return RunSummary.model_validate_json(path.read_bytes())
    first = rows[0] if rows else None
    named = re.search(r"macos-(15|26)$", directory.name)
    return RunSummary(
        status="missing",
        cycles_recorded=len(rows),
        mode="local" if first is not None and first["mode"] == "local" else "hosted",
        host=first["host"] if first is not None else named[1] if named else "local",
        macos="0" if first is None else first["macos"],
        image="unknown" if first is None else first["image"],
    )


def rows_of(directory: Path) -> list[dict[str, str]]:
    path = directory / "cycles.csv"
    return load(path) if path.is_file() else []


def count(rows: Iterable[dict[str, str]]) -> int:
    return sum(1 for _ in rows)


def binomial_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def clopper_pearson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0

    def solve(target: float, k_value: int, upper: bool) -> float:
        low, high = 0.0, 1.0
        for _ in range(200):
            middle = (low + high) / 2
            value = binomial_cdf(k_value, n, middle)
            if (value > target) == upper:
                low = middle
            else:
                high = middle
        return (low + high) / 2

    lower = 0.0 if k == 0 else solve(0.975, k - 1, True)
    upper = 1.0 if k == n else solve(0.025, k, True)
    return round(lower, 6), round(upper, 6)


def criterion_3(rows: list[dict[str, str]]) -> int:
    failures = 0
    for row in rows:
        if row["failure"] == "driver_record_missing":
            continue
        reads = (row["first_open"], row["pre_eject_open"], row["pre_helper_open"])
        failed = (
            "unavailable" in reads
            or row["clearance"] == "unavailable"
            or row["entry_stable"] == "no"
            or row["handles_balanced"] == "no"
        )
        if row["arm"] in {"O", "C"} and row["row"] in {"A1", "H4"}:
            failed = True
        if failed:
            failures += 1
    return failures


def control_state(row: dict[str, str]) -> str:
    if row["clearance"] == "unavailable" or row["entry_stable"] == "no":
        return "criterion_3"
    if row["clearance"] in {"skipped_incomplete", "skipped_budget"}:
        return "inconclusive"
    if (
        row["clearance"] in {"none", "cleared", "persisted"}
        or (row["observer"] == "complete" and row["fixture_holder"] == "no")
        or row["poll_sleeps"] == "1+"
    ):
        return "foreign"
    if row["clearance"] == "skipped_fixture_holder" and row["poll_sleeps"] == "0":
        return "holds"
    return "inconclusive"


def host_complete(rows: list[dict[str, str]], status: str) -> bool:
    expected = media_open_schedule.arms()
    return (
        status == "complete"
        and len(rows) == len(expected)
        and all(
            row["index"] == str(index) and row["arm"] == arm
            for index, (row, arm) in enumerate(zip(rows, expected, strict=True))
        )
    )


def hosted(
    directories: list[Path], local_outcome: str, schedule: Path
) -> dict[str, object]:
    expected = media_open_schedule.read(schedule)
    hosts: dict[str, dict[str, object]] = {}
    cleared_by_host: dict[str, int] = {}
    pooled: list[dict[str, str]] = []
    complete_all = True
    step_one: list[str] = []
    for directory in directories:
        rows = rows_of(directory)
        run = summary(directory, rows)
        for row in rows:
            if row["arm"] != expected[int(row["index"])]:
                raise ValueError("cycle arm differs from the fixed schedule")
        complete = host_complete(rows, run.status)
        complete_all = complete_all and complete
        clearance_rows = [
            row
            for row in rows
            if row["arm"] == "C"
            and row["arm_point"] == "yes"
            and row["clearance"] != "not_run"
        ]
        persisted = count(r for r in clearance_rows if r["clearance"] == "persisted")
        persisted_rate = persisted / len(clearance_rows) if clearance_rows else 0.0
        if (
            complete or persisted >= PERSISTED_DECISIVE
        ) and persisted_rate > PERSISTED_LIMIT:
            step_one.append("criterion_5_host_" + run.host)
        cleared = count(
            r
            for r in rows
            if r["arm"] == "C"
            and r["first_open"] == "true"
            and r["observer"] == "complete"
            and r["fixture_holder"] == "no"
            and r["clearance"] == "cleared"
        )
        controls = [control_state(r) for r in rows if r["arm"] == "control"]
        cleared_by_host[run.host] = cleared
        hosts[run.host] = {
            "status": run.status,
            "complete": complete,
            "macos": run.macos,
            "image": run.image,
            "cycles": len(rows),
            "persisted": persisted,
            "clearance_cycles": len(clearance_rows),
            "cleared_foreign_holds": cleared,
            "controls": {
                state: controls.count(state) for state in sorted(set(controls))
            },
            "rows": {
                arm: {
                    name: count(r for r in rows if r["arm"] == arm and r["row"] == name)
                    for name in ("P1", "A1", "A2", "A3", "A4", "H1", "H2", "H3", "H4")
                }
                for arm in ("O", "C", "control")
            },
        }
        pooled.extend(rows)
    c_rows = [r for r in pooled if r["arm"] == "C"]
    o_rows = [r for r in pooled if r["arm"] == "O"]
    c_qualifying = [r for r in c_rows if r["row"] in QUALIFYING]
    o_qualifying = [r for r in o_rows if r["row"] in QUALIFYING]
    c_refusals = count(r for r in c_qualifying if r["row"] == "H2")
    o_refusals = [r for r in o_qualifying if r["row"] == "H2"]
    o_seen = count(r for r in o_refusals if r["pre_eject_open"] == "true")
    c_events = count(r for r in c_rows if r["row"] in {"A2", "A4", "H3"})
    o_events = count(r for r in o_rows if r["row"] in {"A2", "H3"})
    controls = [control_state(r) for r in pooled if r["arm"] == "control"]
    failure_3 = criterion_3(pooled)
    if local_outcome != "pass":
        step_one.append("local_phase")
    if c_refusals > RESIDUAL_LIMIT:
        step_one.append("residual_refusals")
    if "foreign" in controls:
        step_one.append("control_classed_foreign")
    if failure_3:
        step_one.append("criterion_3")
    incomplete = (
        not complete_all
        or len(directories) != 2
        or set(hosts) != {"15", "26"}
        or any(r["row"] == "A3" for r in pooled)
    )
    step_three: list[str] = []
    step_four: list[str] = []
    if not incomplete:
        if len(o_refusals) >= O_REFUSAL_MINIMUM and o_seen == 0:
            step_three.append("criterion_7_signal_blind")
        if c_events > o_events + DEADLINE_MARGIN:
            step_three.append("criterion_9_deadlines")
        if len(c_qualifying) < QUALIFYING_MINIMUM:
            step_four.append("qualifying_below_760")
        if len(o_refusals) < O_REFUSAL_MINIMUM:
            step_four.append("o_refusals_below_3")
        for host, cleared_count in cleared_by_host.items():
            if cleared_count < CLEARED_MINIMUM:
                step_four.append("criterion_4_host_" + host)
        if "inconclusive" in controls:
            step_four.append("inconclusive_control")
    if step_one:
        outcome = "fail"
    elif incomplete:
        outcome = "inconclusive"
    elif step_three:
        outcome = "fail"
    elif step_four:
        outcome = "inconclusive"
    else:
        outcome = "pass"
    total_refusals = c_refusals + len(o_refusals)
    share = (
        len(c_qualifying) / (len(c_qualifying) + len(o_qualifying))
        if c_qualifying or o_qualifying
        else 0.0
    )
    return {
        "outcome": outcome,
        "step_1_fail": sorted(step_one),
        "step_2_incomplete": incomplete,
        "step_3_fail": sorted(step_three),
        "step_4_inconclusive": sorted(step_four),
        "hosts": hosts,
        "pooled": {
            "qualifying_c": len(c_qualifying),
            "c_refusals": c_refusals,
            "qualifying_o": len(o_qualifying),
            "o_refusals": len(o_refusals),
            "o_refusals_open_true": o_seen,
            "c_events_criterion_9": c_events,
            "o_events_criterion_9": o_events,
            "criterion_3_failures": failure_3,
        },
        "diagnostics": {
            "o_a4_causes": {
                cause: count(
                    r for r in o_rows if r["row"] == "A4" and r["a4_cause"] == cause
                )
                for cause in ("admission_deadline", "lookup_failed", "other")
            },
            "o_refusal_rate_interval": list(
                clopper_pearson(len(o_refusals), len(o_qualifying))
            ),
            "c_vs_o_conditional_p": round(
                binomial_cdf(c_refusals, total_refusals, share), 6
            )
            if total_refusals
            else None,
            "c_refusals_pre_helper_open": {
                value: count(
                    r
                    for r in c_qualifying
                    if r["row"] == "H2" and r["pre_helper_open"] == value
                )
                for value in ("true", "false", "unavailable", "none")
            },
        },
    }


def local(directory: Path, reader_check: Path) -> dict[str, object]:
    rows = rows_of(directory)
    run = summary(directory, rows)
    check = ReaderCheck.model_validate_json(reader_check.read_bytes())
    rest = [r for r in rows if r["arm"] == "rest"]
    holder = [r for r in rows if r["arm"] == "holder"]
    first = rest[:LOCAL_FIRST]
    results: dict[str, str] = {}
    first_ok = len(first) == LOCAL_FIRST and all(
        r["rest_probe"] == "pass" for r in first
    )
    results["L1_first_10"] = "pass" if first_ok else "fail"
    l1_evaluated = [r for r in rest if r["arm_point"] == "yes"]
    l1_pass = count(r for r in l1_evaluated if r["rest_probe"] == "pass")
    results["L1"] = (
        "fail"
        if l1_pass != len(l1_evaluated)
        else "pass"
        if len(l1_evaluated) == LOCAL_REST
        else "inconclusive"
    )
    l2_evaluated = [r for r in holder if r["arm_point"] == "yes"]
    l2_pass = count(
        r
        for r in l2_evaluated
        if r["first_open"] == "true"
        and r["observer"] == "complete"
        and r["fixture_holder"] == "yes"
        and r["clearance"] == "skipped_fixture_holder"
        and r["poll_sleeps"] == "0"
        and r["row"] == "H2"
        and r["code_class"] == "49168"
        and r["dissenter_is_holder"] == "yes"
        and r["after_holder_open"] == "false"
    )
    results["L2"] = (
        "fail"
        if l2_pass != len(l2_evaluated)
        else "pass"
        if len(l2_evaluated) == LOCAL_HOLDER
        else "inconclusive"
    )
    evaluated = [r for r in rows if r["arm_point"] == "yes"]
    short = len(evaluated) != LOCAL_REST + LOCAL_HOLDER

    def judged(passed: int) -> str:
        if passed != len(evaluated) or not evaluated:
            return "fail"
        return "inconclusive" if short else "pass"

    l3 = count(r for r in evaluated if r["fresh_check"] == "equal")
    results["L3"] = judged(l3)
    released = count(
        r
        for r in evaluated
        if r["resources_closed"] == "yes" or r["cycle_cleanup"] == "released"
    )
    balanced = count(r for r in evaluated if r["handles_balanced"] == "yes")
    check_ok = (
        check.handles_balanced == "yes"
        and check.absent_disk == {"found": "no", "read": "unavailable"}
        and check.slice_name == {"found": "no", "read": "unavailable"}
        and check.slice_iokit == {"found": "no", "read": "unavailable"}
        and check.detached_media
        == {
            "found": "yes",
            "read_mounted": "true",
            "read_after_detach": "unavailable",
            "resources_closed": "yes",
        }
    )
    closed_and_balanced = count(
        r
        for r in evaluated
        if r["handles_balanced"] == "yes"
        and (r["resources_closed"] == "yes" or r["cycle_cleanup"] == "released")
    )
    results["L4"] = judged(closed_and_balanced) if check_ok else "fail"
    l5 = count(r for r in evaluated if r["entry_stable"] == "yes")
    results["L5"] = judged(l5)
    values = set(results.values())
    outcome = (
        "fail"
        if "fail" in values
        else "inconclusive"
        if "inconclusive" in values or run.status != "complete"
        else "pass"
    )
    return {
        "outcome": outcome,
        "predicates": results,
        "status": run.status,
        "macos": run.macos,
        "counts": {
            "cycles": len(rows),
            "rest": len(rest),
            "rest_evaluated": len(l1_evaluated),
            "rest_pass": l1_pass,
            "holder": len(holder),
            "holder_evaluated": len(l2_evaluated),
            "holder_pass": l2_pass,
            "fresh_equal": l3,
            "arm_point": len(evaluated),
            "handles_balanced": balanced,
            "released": released,
            "entry_stable": l5,
            "reader_check": "pass" if check_ok else "fail",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("hosted", "local"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", type=Path, action="append", default=[])
    parser.add_argument("--local-directory", type=Path)
    parser.add_argument("--reader-check", type=Path)
    parser.add_argument("--local-outcome", choices=("pass", "fail", "inconclusive"))
    parser.add_argument("--schedule", type=Path)
    arguments = parser.parse_args()
    output: Path = arguments.output
    if arguments.mode == "local":
        directory: Path | None = arguments.local_directory
        check: Path | None = arguments.reader_check
        if directory is None or check is None:
            raise SystemExit("local mode needs the cycle directory and reader check")
        verdict = local(directory, check)
    else:
        hosts: list[Path] = arguments.host
        local_outcome: str | None = arguments.local_outcome
        if local_outcome is None:
            raise SystemExit("hosted mode needs the local phase outcome")
        schedule: Path | None = arguments.schedule
        if schedule is None:
            raise SystemExit("hosted mode needs the schedule file")
        verdict = hosted(hosts, local_outcome, schedule)
    output.write_text(json.dumps(verdict, sort_keys=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
