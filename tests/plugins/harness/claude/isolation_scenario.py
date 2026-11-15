import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import mkdtemp

from kora.conformance.capture import captured
from kora.conformance.common import utc_now
from kora.conformance.harness import ConformancePolicy, ConformanceSink, Trace
from kora.observability import LoggingSettings
from kora.plugins.harness.claude.session import COMPONENT
from kora.protocols.time import SystemClock
from tests.plugins.harness.claude.scripted_launch import ScriptedLaunches
from tests.plugins.harness.claude.scripted_model import (
    ScriptedState,
    mapping,
    objects,
    serve,
    stop_server,
)
from tests.plugins.harness.claude.test_claude_conformance import make_adapter
from tests.plugins.harness.claude.test_replay_events import context_for
from tests.plugins.harness.claude.test_session_lifecycle import session_spec


def scripted_calls(value: object) -> list[tuple[str, dict[str, object]]]:
    calls: list[tuple[str, dict[str, object]]] = []
    entries = objects(value)
    assert entries is not None
    for item in entries:
        pair = objects(item)
        assert pair is not None
        assert len(pair) == 2
        name, arguments = pair
        assert isinstance(name, str)
        payload = mapping(arguments)
        assert payload is not None
        calls.append((name, payload))
    return calls


async def scenario(config: dict[str, object]) -> dict[str, object]:
    workspace = config["workspace"]
    markers = config["markers"]
    assert isinstance(workspace, str)
    assert isinstance(markers, str)
    state = ScriptedState(scripted_calls(config["calls"]))
    server, thread, base_url = serve(state)
    launches = ScriptedLaunches(
        Path(mkdtemp(prefix="ki", dir="/tmp")), Path(workspace), base_url
    )
    stderr = os.fstat(2)
    try:
        with captured(COMPONENT, LoggingSettings(), "isolation", utc_now) as capture:
            adapter = make_adapter(Path(workspace), context_for(capture, SystemClock()))
            sink = ConformanceSink(None, Trace())
            policy = ConformancePolicy("Forbidden", sink.trace)

            async with asyncio.timeout(60):
                result = await adapter.run(
                    session_spec(adapter), policy, sink, launches
                )

            frames = [
                mapping(json.loads(line))
                for line in launches.stream.read_text().splitlines()
            ]
            init = next(
                frame
                for frame in frames
                if frame is not None and frame.get("subtype") == "init"
            )
            config_settings = launches.placements["harness_config"] / "settings.json"
            return {
                "config_settings": config_settings.read_text()
                if config_settings.exists()
                else None,
                "cause": result.stop_reason,
                "error_class": result.error_class,
                "policy_bypass": result.policy_bypass,
                "denials": result.denials.model_dump(mode="json"),
                "init": {
                    key: init.get(key)
                    for key in (
                        "plugins",
                        "tools",
                        "mcp_servers",
                        "skills",
                        "agents",
                        "slash_commands",
                    )
                },
                "recorder": launches.recorder_report(),
                "starts": len(launches.starts.read_text().splitlines()),
                "requests": list(state.bodies),
                "clients": list(state.clients),
                "events": [event.model_dump(mode="json") for event in sink.events],
                "placements": {
                    name: str(path) for name, path in launches.placements.items()
                },
                "base_url": base_url,
                "child_stderr": [stderr.st_dev, stderr.st_ino],
                "markers": [str(path) for path in Path(markers).iterdir()],
                "control_socket": str(launches.root / "s"),
                "report_path": str(launches.report_path),
            }
    finally:
        await launches.close()
        stop_server(server, thread)


if __name__ == "__main__":
    configuration = mapping(json.loads(Path(sys.argv[1]).read_text()))
    assert configuration is not None
    report = configuration["report"]
    assert isinstance(report, str)
    Path(report).write_text(json.dumps(asyncio.run(scenario(configuration))))
