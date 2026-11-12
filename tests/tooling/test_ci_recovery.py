import dataclasses
import datetime
import json
import urllib.parse
from pathlib import Path

import pytest

from scripts.ci.ci_recovery import (
    Client,
    Evidence,
    Job,
    RecoveryError,
    Run,
    Step,
    classify,
    integer,
    mapping,
    optional_text,
    sequence,
    text,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ci_recovery"
BEARER = "bearer-value"


def parse_fixture_run(value: object) -> Run:
    data = mapping(value)
    return Run(
        id=integer(data["id"]),
        run_attempt=integer(data["run_attempt"]),
        conclusion=optional_text(data["conclusion"]),
        event=text(data["event"]),
        head_branch=text(data["head_branch"]),
        head_sha=text(data["head_sha"]),
        tree_id=text(data["tree_id"]),
        head_repository_id=integer(data["head_repository_id"]),
        repository_id=integer(data["repository_id"]),
        created_at=datetime.datetime.fromisoformat(
            text(data["created_at"]).replace("Z", "+00:00")
        ),
        name=text(data["name"]),
    )


def parse_fixture_job(value: object) -> Job:
    data = mapping(value)
    return Job(
        id=integer(data["id"]),
        name=text(data["name"]),
        conclusion=optional_text(data["conclusion"]),
        runner_name=text(data["runner_name"]),
        runner_id=integer(data["runner_id"]),
        labels=tuple(text(label) for label in sequence(data["labels"])),
        steps=tuple(
            Step(
                text(mapping(step)["name"]), optional_text(mapping(step)["conclusion"])
            )
            for step in sequence(data["steps"])
        ),
    )


def recorded(name: str) -> Evidence:
    data = mapping(json.loads((FIXTURES / f"{name}.json").read_text()))
    run = parse_fixture_run(data["run"])
    return Evidence(
        run=run,
        jobs=tuple(parse_fixture_job(job) for job in sequence(data["jobs"])),
        annotations={
            int(key): tuple(text(item) for item in sequence(value))
            for key, value in mapping(data["annotations"]).items()
        },
        logs={
            int(key): "\n".join(text(item) for item in sequence(value))
            for key, value in mapping(data["logs"]).items()
        },
        history=(run,),
        ledger_complete=True,
        gate_inputs_unchanged=True,
    )


def first_attempt(evidence: Evidence) -> Evidence:
    run = dataclasses.replace(evidence.run, run_attempt=1, conclusion="failure")
    return dataclasses.replace(evidence, run=run, history=(run,))


def other_run(evidence: Evidence, head_sha: str, tree_id: str, offset: int) -> Run:
    return dataclasses.replace(
        evidence.run,
        id=evidence.run.id + offset,
        head_sha=head_sha,
        tree_id=tree_id,
        created_at=evidence.run.created_at + datetime.timedelta(minutes=offset),
    )


def kinds(evidence: Evidence) -> dict[str, str]:
    return {item.job.name: item.kind for item in classify(evidence).jobs}


def outage() -> Evidence:
    base = recorded("unacquired_general_lanes")

    def join(job_id: int, name: str, gate: str) -> Job:
        return Job(
            job_id,
            name,
            "failure",
            "GitHub Actions 1",
            1,
            ("ubuntu-24.04",),
            (Step("Set up job", "success"), Step(gate, "failure")),
        )

    jobs = (
        join(1, "Scaffold evidence", "Require all test lanes and collection evidence"),
        join(
            2,
            "Scaffold (ubuntu-24.04, Python 3.11.17)",
            "Require verified scaffold evidence",
        ),
        join(
            3,
            "Scaffold (ubuntu-24.04, Python 3.15.0)",
            "Require verified scaffold evidence",
        ),
        join(
            4,
            "Scaffold (macos-15, Python 3.11.17)",
            "Require verified scaffold evidence",
        ),
        join(5, "CEL evaluator lifetime", "Require every measurement and artifact"),
        join(
            6,
            "Console browser checks",
            "Require browser verification or approved editorial scope",
        ),
    )
    return dataclasses.replace(base, jobs=jobs, annotations={}, logs={})


def test_unacquired_storage_lane_reruns() -> None:
    evidence = recorded("unacquired_storage_lane")

    decision = classify(evidence)

    assert decision.verdict == "rerun"
    assert (
        kinds(evidence)["Scaffold tests (macos-15, Python 3.11.17, storage)"]
        == "no_runner"
    )
    assert kinds(evidence)["Scaffold evidence"] == "dependent"


@pytest.mark.parametrize(
    "name", ["unacquired_general_lanes", "unacquired_general_lanes_second"]
)
def test_unacquired_general_lanes_with_timing_refusal_rerun(name: str) -> None:
    evidence = recorded(name)

    decision = classify(evidence)

    assert decision.verdict == "rerun"
    assert {item.kind for item in decision.jobs} == {"no_runner", "dependent"}
    assert kinds(evidence)["CEL evaluator lifetime"] == "dependent"
    assert kinds(evidence)["Scaffold (macos-15, Python 3.11.17)"] == "dependent"


def test_run_without_root_jobs_and_failing_joins_reruns() -> None:
    evidence = outage()

    decision = classify(evidence)

    assert decision.verdict == "rerun"
    assert {item.kind for item in decision.jobs} == {"dependent"}


def test_failed_test_lane_does_not_rerun() -> None:
    evidence = recorded("failed_test_lane")

    decision = classify(evidence)

    assert decision.verdict == "no_rerun"
    assert decision.reason == "Scaffold tests (macos-15, Python 3.11.17, storage)"


def test_deadline_cancellation_during_tests_does_not_rerun() -> None:
    evidence = recorded("deadline_during_tests")

    skipped = classify(evidence)
    classified = classify(first_attempt(evidence))

    assert skipped.verdict == "skip"
    assert classified.verdict == "no_rerun"
    assert (
        kinds(first_attempt(evidence))[
            "Scaffold tests (macos-15, Python 3.11.17, general-2)"
        ]
        == "kora"
    )


def test_deadline_cancellation_after_tests_does_not_rerun() -> None:
    evidence = first_attempt(recorded("download_failure_and_deadline"))

    decision = classify(evidence)

    assert decision.verdict == "no_rerun"
    assert (
        kinds(evidence)["Scaffold tests (ubuntu-24.04, Python 3.11.17, local-runtime)"]
        == "kora"
    )


def test_download_failure_does_not_rerun() -> None:
    evidence = first_attempt(recorded("download_failure_and_deadline"))

    decision = classify(evidence)

    assert decision.verdict == "no_rerun"
    assert kinds(evidence)["Mermaid diagrams"] == "kora"


def test_superseded_pull_request_run_does_not_rerun() -> None:
    recorded_run = recorded("superseded_pull_request")
    evidence = first_attempt(recorded("unacquired_storage_lane"))
    pull_request = dataclasses.replace(
        evidence.run, event="pull_request", head_branch="topic"
    )
    evidence = dataclasses.replace(evidence, run=pull_request, history=(pull_request,))
    newer = other_run(evidence, "1" * 40, "2" * 40, 1)

    decision = classify(dataclasses.replace(evidence, history=(pull_request, newer)))

    assert classify(recorded_run).verdict == "skip"
    assert decision.verdict == "skip"
    assert decision.reason == "superseded"


def test_second_attempt_does_not_rerun() -> None:
    evidence = recorded("unacquired_storage_lane")
    second = dataclasses.replace(evidence.run, run_attempt=2)

    decision = classify(dataclasses.replace(evidence, run=second, history=(second,)))

    assert decision.verdict == "skip"
    assert decision.reason == "attempt"


def test_empty_commit_with_same_tree_does_not_rerun() -> None:
    evidence = recorded("unacquired_storage_lane")
    earlier = other_run(evidence, "3" * 40, evidence.run.tree_id, -1)

    decision = classify(dataclasses.replace(evidence, history=(earlier, evidence.run)))

    assert decision.verdict == "skip"
    assert decision.reason == "limit_used"


def test_master_run_with_pull_request_tree_is_not_counted() -> None:
    evidence = recorded("unacquired_storage_lane")
    pull_request = dataclasses.replace(
        other_run(evidence, "4" * 40, evidence.run.tree_id, -1),
        event="pull_request",
        head_branch="topic",
    )

    decision = classify(
        dataclasses.replace(evidence, history=(pull_request, evidence.run))
    )

    assert decision.verdict == "rerun"


def test_incomplete_ledger_listing_does_not_rerun() -> None:
    evidence = recorded("unacquired_storage_lane")

    decision = classify(dataclasses.replace(evidence, ledger_complete=False))

    assert decision.verdict == "skip"
    assert decision.reason == "ledger_incomplete"


def test_unacquired_lane_plus_failed_test_does_not_rerun() -> None:
    evidence = recorded("unacquired_lanes_with_failed_local_runtime")

    decision = classify(evidence)

    assert decision.verdict == "no_rerun"
    assert "local-runtime" in decision.reason


def test_timing_refusal_other_than_missing_lane_does_not_rerun() -> None:
    evidence = recorded("unacquired_general_lanes")
    evidence_job = next(job for job in evidence.jobs if job.name == "Scaffold evidence")
    logs = {evidence_job.id: "the lane duration table lacks an environment"}

    decision = classify(dataclasses.replace(evidence, logs=logs))

    assert decision.verdict == "no_rerun"
    assert decision.reason == "Scaffold evidence"


def test_evidence_log_line_with_kora_lane_does_not_rerun() -> None:
    evidence = recorded("unacquired_lanes_with_failed_local_runtime")

    decision = classify(evidence)

    assert decision.verdict == "no_rerun"
    assert kinds(evidence)["Scaffold evidence"] == "kora"


def test_unannotated_cancellation_does_not_rerun() -> None:
    evidence = recorded("unacquired_storage_lane")

    decision = classify(dataclasses.replace(evidence, annotations={}))

    assert decision.verdict == "no_rerun"


def test_zero_job_cancelled_run_does_not_rerun() -> None:
    evidence = recorded("zero_job_cancelled")
    failed_run = first_attempt(evidence)

    assert classify(evidence).verdict == "skip"
    assert classify(failed_run).reason == "no_jobs"


def test_unexpected_runner_label_does_not_rerun() -> None:
    evidence = recorded("unacquired_storage_lane")
    jobs = tuple(
        dataclasses.replace(job, labels=("self-hosted",))
        if job.steps == () and job.runner_id == 0
        else job
        for job in evidence.jobs
    )

    decision = classify(dataclasses.replace(evidence, jobs=jobs))

    assert decision.verdict == "no_rerun"


def test_changed_gate_inputs_are_skipped() -> None:
    evidence = recorded("unacquired_storage_lane")

    decision = classify(dataclasses.replace(evidence, gate_inputs_unchanged=False))

    assert decision.verdict == "skip"
    assert decision.reason == "workflow_changed"


def test_continue_on_error_diagnostic_does_not_decide() -> None:
    evidence = recorded("unacquired_storage_lane")
    diagnostic = Job(
        9,
        "Local runtime diagnostics (macos-15, Python 3.11.17)",
        "failure",
        "runner",
        3,
        ("macos-15",),
        (Step("Run diagnostics", "failure"),),
    )

    decision = classify(
        dataclasses.replace(evidence, jobs=(*evidence.jobs, diagnostic))
    )

    assert decision.verdict == "rerun"
    assert diagnostic.name not in kinds(
        dataclasses.replace(evidence, jobs=(*evidence.jobs, diagnostic))
    )


def test_fork_run_is_skipped() -> None:
    evidence = recorded("unacquired_storage_lane")
    fork = dataclasses.replace(evidence.run, head_repository_id=1)

    decision = classify(dataclasses.replace(evidence, run=fork, history=(fork,)))

    assert decision.verdict == "skip"
    assert decision.reason == "fork"


def test_token_header_is_not_redirected() -> None:
    client = Client("realkarych/kora", BEARER)

    request = client.request(client.url("actions/runs/1"))

    assert "Authorization" not in request.headers
    assert request.unredirected_hdrs["Authorization"] == f"Bearer {BEARER}"
    assert urllib.parse.urlsplit(request.full_url).hostname == "api.github.com"


def test_request_outside_the_api_host_is_refused() -> None:
    client = Client("realkarych/kora", BEARER)

    with pytest.raises(RecoveryError, match="outside the API host"):
        client.request("https://example.com/repos/realkarych/kora/actions/runs/1")


def test_client_refuses_unlisted_paths_and_values() -> None:
    client = Client("realkarych/kora", BEARER)

    with pytest.raises(RecoveryError, match="unlisted repository path"):
        client.blob("README.md", "5" * 40)
    with pytest.raises(RecoveryError, match="commit or tree SHA"):
        client.blob(".github/workflows/ci.yml", "master")
    with pytest.raises(RecoveryError, match="positive integer"):
        client.rerun(0)
