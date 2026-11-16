import json
import subprocess
import sys
from collections.abc import Generator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from kora.conformance.capture import Capture, captured
from kora.conformance.common import utc_now
from kora.conformance.harness import ConformanceSink, Trace
from kora.ir import ErrorClass
from kora.ir.json import parse_json_object
from kora.observability import LoggingSettings
from kora.observability.redaction import SecretRegistry
from kora.plugins.harness.claude.denials import harness_denials
from kora.plugins.harness.claude.events import ToolPiece
from kora.plugins.harness.claude.replay import (
    RECORDING_ENV,
    player_text,
    run_replayed_session,
)
from kora.plugins.harness.claude.sdk import Decision, run_replayed_client
from kora.plugins.harness.claude.session import COMPONENT
from kora.plugins.harness.claude.stop import ClaudeObservation
from kora.protocols.errors import KoraProtocolError
from kora.protocols.harness import MAX_SAMPLE_ITEMS, PermissionDeniedEvent, RunnerCause
from kora.protocols.time import SystemClock
from tests.plugins.harness.claude.test_replay_events import (
    context_for,
    init_fields,
    projector,
    rendered_events,
)
from tests.plugins.harness.claude.test_session_lifecycle import run_lifecycle

RECORDINGS = Path(__file__).resolve().parent / "recordings"
CANARY = "hidden-tool-input-stop-denials"
PENDING_LEGS: dict[str, str] = {}


@dataclass(frozen=True)
class StopCase:
    leg: str
    recording: str | None
    cause: RunnerCause
    expected: ErrorClass | None


STOP_CASES = (
    StopCase("success", "success-false", "completed", None),
    StopCase("success-error", "success-true", "completed", ErrorClass.HARNESS_ERROR),
    *(
        StopCase(f"{subtype}-{error}", f"{subtype}-{error}", "completed", expected)
        for subtype, expected in (
            ("error_max_budget_usd", ErrorClass.BUDGET_EXHAUSTED),
            ("error_max_turns", ErrorClass.EVENTS_EXHAUSTED),
            ("error_max_structured_output_retries", ErrorClass.ARTIFACT_INVALID),
            ("error_during_execution", ErrorClass.HARNESS_ERROR),
            ("unknown", ErrorClass.HARNESS_ERROR),
        )
        for error in ("false", "true")
    ),
    StopCase(
        "terminal-max-turns",
        "terminal-max-turns",
        "completed",
        ErrorClass.EVENTS_EXHAUSTED,
    ),
    *(
        StopCase(error, error, "completed", ErrorClass.CONFIG_ERROR)
        for error in (
            "authentication_failed",
            "billing_error",
            "account_on_hold",
            "credits_required",
        )
    ),
    StopCase("overloaded", "overloaded", "completed", ErrorClass.HARNESS_ERROR),
    *(
        StopCase(signal, signal, "completed", ErrorClass.PROVIDER_LIMITED)
        for signal in ("rate_limit", "rate-rejected", "api-429")
    ),
    StopCase("retry-success", "retry-success", "completed", None),
    StopCase(
        "provider-interrupt",
        "interrupt-aborted_tools",
        "provider_limit",
        ErrorClass.PROVIDER_LIMITED,
    ),
    StopCase(
        "cancel-interrupt",
        "interrupt-aborted_streaming",
        "cancel",
        ErrorClass.CANCELLED,
    ),
    StopCase(
        "policy-bypass", "undecided-success", "completed", ErrorClass.HARNESS_ERROR
    ),
    StopCase(
        "limit-then-bypass", "undecided-success", "budget", ErrorClass.HARNESS_ERROR
    ),
    StopCase(
        "limit-then-provider",
        "limit-then-provider",
        "budget",
        ErrorClass.BUDGET_EXHAUSTED,
    ),
    StopCase(
        "provider-cause-bypass",
        "undecided-success",
        "provider_limit",
        ErrorClass.HARNESS_ERROR,
    ),
    StopCase(
        "provider-and-bypass", "rate-bypass", "completed", ErrorClass.HARNESS_ERROR
    ),
    StopCase("launch-rejected", None, "launch_rejected", ErrorClass.CONFIG_ERROR),
    StopCase("kill-no-result", "no-result", "deadline", ErrorClass.TIMEOUT),
    StopCase(
        "cli-exit-no-result",
        "no-result",
        "cli_exit_without_result",
        ErrorClass.HARNESS_ERROR,
    ),
    StopCase("launcher-death", "no-result", "launcher_died", ErrorClass.INFRASTRUCTURE),
    StopCase("failed-launch", None, "launch_failed", ErrorClass.INFRASTRUCTURE),
    StopCase("completed-no-result", "no-result", "completed", ErrorClass.HARNESS_ERROR),
)


@dataclass(frozen=True)
class DenialCase:
    leg: str
    total: int = 0
    sampled: int = 0
    unmatched: int = 0
    complete: bool = True
    bypass: bool = False
    unconfirmed: int = 0


DENIAL_CASES = (
    DenialCase("settings-denial", total=1, sampled=1),
    DenialCase("callback-deny"),
    DenialCase("callback-allow", total=1, sampled=1),
    DenialCase("pre-execution"),
    DenialCase("pre-execution-denial", total=1, sampled=1),
    DenialCase("embedded-marker", bypass=True),
    DenialCase("mcp-marker", bypass=True),
    DenialCase("undecided-error", bypass=True),
    DenialCase("held-error", complete=False, unconfirmed=1),
    DenialCase("undecided-success", bypass=True),
    DenialCase("unmatched-denial", unmatched=1),
    DenialCase("oversized-name", total=1, complete=False),
    DenialCase("unknown-marker", bypass=True),
)
SECTION_13_LEGS = frozenset(
    {
        "success",
        "success-error",
        "terminal-max-turns",
        "authentication_failed",
        "billing_error",
        "account_on_hold",
        "credits_required",
        "overloaded",
        "rate_limit",
        "rate-rejected",
        "api-429",
        "retry-success",
        "provider-interrupt",
        "cancel-interrupt",
        "policy-bypass",
        "limit-then-bypass",
        "provider-cause-bypass",
        "limit-then-provider",
        "provider-and-bypass",
        "launch-rejected",
        "kill-no-result",
        "cli-exit-no-result",
        "launcher-death",
        "failed-launch",
        "completed-no-result",
        "settings-denial",
        "callback-deny",
        "callback-allow",
        "pre-execution",
        "pre-execution-denial",
        "embedded-marker",
        "mcp-marker",
        "undecided-error",
        "held-error",
        "undecided-success",
        "unmatched-denial",
        "oversized-name",
        "unknown-marker",
        "scripted-execution-error",
        "bypass-interrupt",
        "success-racing-real-interrupt",
        "error_max_budget_usd-false",
        "error_max_budget_usd-true",
        "error_max_turns-false",
        "error_max_turns-true",
        "error_max_structured_output_retries-false",
        "error_max_structured_output_retries-true",
        "error_during_execution-false",
        "error_during_execution-true",
        "unknown-false",
        "unknown-true",
    }
)


@pytest.fixture
def stop_capture() -> Generator[Capture]:
    with captured(COMPONENT, LoggingSettings(), "claude-stop", utc_now) as capture:
        yield capture


def recording_path(name: str) -> Path:
    return RECORDINGS / f"constructed-{name}-2.1.301.ndjson"


def unlaunched_observation() -> tuple[ConformanceSink, ClaudeObservation]:
    sink, events = projector()
    return sink, events.observation()


async def replay_recording(
    name: str | None, capture: Capture, root: Path
) -> tuple[ConformanceSink, ClaudeObservation]:
    sink = ConformanceSink(None, Trace())
    if name is None:
        return unlaunched_observation()
    work = root / "work"
    work.mkdir()
    calls: list[tuple[str, str]] = []

    def decide(tool: str, identity: str) -> Decision:
        calls.append((tool, identity))
        return "deny" if name == "callback-deny" else "allow"

    observation = await run_replayed_session(
        context_for(capture, SystemClock()),
        sink,
        SecretRegistry(),
        cwd=work,
        launch_dir=root / "launch",
        recording=recording_path(name),
        environment={},
        prompt="replay classification",
        tools=("Bash",),
        bound=timedelta(seconds=30),
        output_format=False,
        decide=decide,
    )
    assert calls == ([("Bash", "call-1")] if name.startswith("callback-") else [])
    assert not (root / "launch" / "environment.json").exists()
    assert CANARY not in rendered_events(sink)
    return sink, observation


@pytest.mark.asyncio
@pytest.mark.parametrize("case", STOP_CASES, ids=[case.leg for case in STOP_CASES])
async def test_claude_stop_reason_mapping(
    case: StopCase, stop_capture: Capture, tmp_path: Path
) -> None:
    expected = case.expected

    _sink, observation = await replay_recording(case.recording, stop_capture, tmp_path)

    assert observation.error_class(case.cause) == expected
    if case.leg == "limit-then-provider":
        assert observation.provider_limit_seen
    assert observation.api_error_status == (
        429 if case.recording == "api-429" else None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", DENIAL_CASES, ids=[case.leg for case in DENIAL_CASES])
async def test_claude_harness_denials_are_recorded(
    case: DenialCase, stop_capture: Capture, tmp_path: Path
) -> None:
    expected = case

    sink, observation = await replay_recording(case.leg, stop_capture, tmp_path)

    assert_denial(sink, observation, expected)


def assert_denial(
    sink: ConformanceSink, observation: ClaudeObservation, case: DenialCase
) -> None:
    denials = [
        event for event in sink.events if isinstance(event, PermissionDeniedEvent)
    ]
    assert len(denials) == case.sampled
    assert observation.denials.total == case.total
    assert len(observation.denials.items) == case.sampled
    assert observation.unmatched_denials == case.unmatched
    assert observation.denials_complete is case.complete
    assert observation.bypass.confirmed is case.bypass
    assert observation.bypass.unconfirmed.total == case.unconfirmed
    assert CANARY not in rendered_events(sink)
    for event in denials:
        assert event.data.source == "harness"
        assert event.data.pattern is None
        assert event.data.code == "harness_rule"
        assert event.data.tool_use_id == "call-1"
        assert sink.events.index(event) < len(sink.events) - 1
    if case.unconfirmed:
        assert observation.error_class("deadline") == ErrorClass.TIMEOUT


def test_section_13_legs_have_cases_or_pending_owners() -> None:
    case_ids = {case.leg for case in STOP_CASES} | {case.leg for case in DENIAL_CASES}

    covered = (
        case_ids
        | PENDING_LEGS.keys()
        | {
            "bypass-interrupt",
            "success-racing-real-interrupt",
            "scripted-execution-error",
        }
    )

    assert SECTION_13_LEGS == covered
    assert not case_ids.intersection(PENDING_LEGS)
    assert all(owner.startswith("#") for owner in PENDING_LEGS.values())


@pytest.mark.parametrize(
    "entries", [{}, [None], [{}], [{"tool_name": 1, "tool_use_id": "call"}]]
)
def test_malformed_denials_fail_without_content(entries: object) -> None:
    observed = {"call": "Bash"}

    with pytest.raises(KoraProtocolError) as caught:
        harness_denials(entries, observed, set())

    assert caught.value.code == "harness_error"
    assert caught.value.details == {}


def test_denial_sample_overflow_preserves_held_classification() -> None:
    _sink, events = projector()
    events.observe_init(init_fields())
    entries: list[dict[str, object]] = []
    for index in range(MAX_SAMPLE_ITEMS + 1):
        identity = f"call-{index}"
        events.assistant(f"message-{index}", None, (ToolPiece("Bash", identity),))
        if index < MAX_SAMPLE_ITEMS:
            events.decided(identity, True)
        events.tool_result(identity, True, "denied")
        entries.append(
            {"tool_name": "Bash", "tool_use_id": identity, "tool_input": CANARY}
        )

    events.observe_result("success", False, None, None, permission_denials=entries)
    events.finish()
    observation = events.observation()

    assert observation.denials.total == MAX_SAMPLE_ITEMS + 1
    assert len(observation.denials.items) == MAX_SAMPLE_ITEMS
    assert observation.denials_complete is False
    assert observation.bypass.confirmed is False


def test_held_error_overflow_is_a_bypass_without_result() -> None:
    _sink, events = projector()
    events.observe_init(init_fields())

    for index in range(MAX_SAMPLE_ITEMS + 1):
        events.tool_result(f"call-{index}", True, "failed")
    observation = events.observation()

    assert observation.bypass.confirmed is True
    assert observation.bypass.unconfirmed.total == MAX_SAMPLE_ITEMS
    assert observation.denials_complete is False
    assert observation.error_class("budget") == ErrorClass.HARNESS_ERROR


@pytest.mark.parametrize(
    "tool", ["", "x" * 257, "has space", "nonascii-λ", "line\nbreak"]
)
def test_unrepresentable_denial_names_are_only_counted(tool: str) -> None:
    entry = {"tool_name": tool, "tool_use_id": "call", "tool_input": CANARY}

    denials = harness_denials([entry], {"call": "Bash"}, set())

    assert denials.sample.total == 1
    assert denials.sample.items == ()
    assert denials.matched == {"call"}
    assert denials.unmatched == 0


def test_conflicting_tool_identity_is_refused() -> None:
    _sink, events = projector()
    events.observe_init(init_fields())
    events.assistant("message", None, (ToolPiece("mcp__server__tool", "call"),))

    with pytest.raises(KoraProtocolError) as caught:
        events.assistant("message", None, (ToolPiece("Bash", "call"),))

    assert caught.value.code == "harness_error"


def test_non_error_bypass_is_visible_before_result() -> None:
    _sink, events = projector()
    events.observe_init(init_fields())
    events.assistant("message", None, (ToolPiece("Bash", "call"),))

    events.tool_result("call", False, "done")

    assert events.bypass_confirmed is True
    assert events.observation().result_seen is False


@pytest.mark.parametrize("response", [None, "wrong-id", "hook-request-1"])
def test_player_requires_matching_hook_response(
    tmp_path: Path, response: str | None
) -> None:
    player = tmp_path / "player"
    player.write_text(player_text(sys.executable))
    messages: list[dict[str, object]] = [
        {
            "type": "control_request",
            "request_id": "initialize",
            "request": {"subtype": "initialize"},
        },
        {"type": "user"},
    ]
    if response is not None:
        messages.append(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": response,
                    "response": {},
                },
            }
        )
    request = "".join(json.dumps(message) + "\n" for message in messages)

    completed = subprocess.run(
        [sys.executable, "-I", str(player)],
        input=request,
        text=True,
        capture_output=True,
        env={RECORDING_ENV: str(recording_path("callback-allow"))},
        timeout=10,
        check=False,
    )

    assert completed.returncode == (0 if response == "hook-request-1" else 1)
    kinds = [
        parse_json_object(json.loads(line))["type"]
        for line in completed.stdout.splitlines()
    ]
    assert "control_request" in kinds
    assert ("result" in kinds) is (response == "hook-request-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "invalid-decision"])
async def test_sdk_hook_failure_denies_without_sending_exception_text(
    tmp_path: Path, failure: str
) -> None:
    _sink, events = projector()
    player = tmp_path / "player"
    source = player_text(sys.executable).replace(
        "os.environ['KORA_CLAUDE_REPLAY_RECORDING']",
        repr(str(recording_path("callback-allow"))),
    )
    source = source.replace(
        "import sys\n",
        "import sys\nif sys.argv[1:] == ['-v']:\n    raise SystemExit(0)\n",
    ).replace(
        "            break\n",
        "            expected = {'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', "
        "'permissionDecisionReason': 'kora'}}\n"
        "            if response.get('response') != expected:\n"
        "                raise SystemExit(1)\n"
        "            break\n",
    )
    player.write_text(source)
    player.chmod(0o700)
    clock = SystemClock()

    def decide(_tool: str, _identity: str) -> Decision:
        if failure == "exception":
            raise ValueError(CANARY)
        return cast(Decision, "invalid")

    with pytest.raises(KoraProtocolError) as caught:
        await run_replayed_client(
            events,
            cwd=tmp_path,
            launcher=player,
            launch_dir=tmp_path,
            prompt="replay hook failure",
            tools=("Bash",),
            decide=decide,
            clock=clock,
            deadline_ns=clock.monotonic_ns() + 10_000_000_000,
        )

    assert caught.value.code == "harness_error"
    assert CANARY not in str(caught.value)
    assert events.observation().result_seen is True


@pytest.mark.asyncio
async def test_bypass_interrupt(stop_capture: Capture) -> None:

    result, outcome = await run_lifecycle(stop_capture, "bypass")

    assert result.policy_bypass
    assert result.error_class is ErrorClass.HARNESS_ERROR
    assert outcome.cause == "policy_bypass"
    assert outcome.termination == "interrupted"


@pytest.mark.asyncio
async def test_success_racing_real_interrupt(stop_capture: Capture) -> None:

    result, outcome = await run_lifecycle(stop_capture, "race", cause="deadline")

    assert result.error_class is ErrorClass.TIMEOUT
    assert outcome.cause == "deadline"
    assert outcome.termination == "interrupted"


def test_scripted_execution_error(tmp_path: Path) -> None:
    from tests.plugins.harness.claude.contained import assert_no_outbound
    from tests.plugins.harness.claude.scripted_model import mapping, objects
    from tests.plugins.harness.claude.test_session_isolation import run_scenario

    workspace, account, markers = (
        tmp_path / name for name in ("workspace", "account", "markers")
    )
    for path in (workspace, account, markers):
        path.mkdir()

    report = run_scenario(
        tmp_path, workspace, account, markers, [("Bash", {"command": "exit 3"})]
    )

    assert_no_outbound(report)
    assert report["cause"] == "completed"
    assert report["error_class"] is None
    assert report["policy_bypass"] is False
    assert (mapping(report["denials"]) or {})["total"] == 0
    events = [mapping(event) or {} for event in objects(report["events"]) or []]
    results = [
        mapping(event["data"]) or {}
        for event in events
        if event.get("kind") == "tool_result"
    ]
    assert len(results) == 1
    assert results[0]["is_error"] is True
    text = results[0]["content"]
    assert isinstance(text, str)
    assert text
    assert not text.startswith("<tool_use_error>")
    assert not any(event.get("kind") == "permission_denied" for event in events)
