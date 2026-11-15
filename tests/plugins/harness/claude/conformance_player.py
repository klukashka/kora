import json
import os
import sys
from pathlib import Path
from typing import cast


def read_message() -> dict[str, object]:
    raw = sys.stdin.readline()
    if not raw:
        raise SystemExit(0)
    message: object = json.loads(raw)
    if not isinstance(message, dict):
        raise ValueError("invalid replay input")
    return cast("dict[str, object]", message)


def send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def control(
    message: dict[str, object], version: str, ignore: bool = False
) -> str | None:
    request = message.get("request")
    if message.get("type") != "control_request" or not isinstance(request, dict):
        return None
    subtype = cast("dict[str, object]", request).get("subtype")
    if subtype not in {"initialize", "interrupt"}:
        raise ValueError("unexpected replay control")
    if subtype == "initialize" and version == "ignore_initialize":
        os.write(int(sys.argv[2]), b"initializing\n")
        return "initialize"
    if subtype == "interrupt":
        os.write(int(sys.argv[2]), b"interrupt\n")
    if ignore and subtype == "interrupt":
        return "interrupt"
    send(
        {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": message["request_id"],
                "response": {"claude_code_version": version}
                if subtype == "initialize"
                else {},
            },
        }
    )
    return str(subtype)


def play(recording: Path, version: str) -> None:
    while True:
        message = read_message()
        if control(message, version) is not None:
            continue
        if message.get("type") != "user":
            raise ValueError("expected replay prompt")
        recording.with_suffix(".prompt").write_text("received")
        break
    interrupted = False
    with recording.open() as handle:
        for line in handle:
            value: object = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("invalid replay frame")
            frame = cast("dict[str, object]", value)
            directive = frame.get("kora_replay")
            if directive in {"await_interrupt", "ignore_interrupt"}:
                while not interrupted or directive == "ignore_interrupt":
                    interrupted |= (
                        control(
                            read_message(), version, directive == "ignore_interrupt"
                        )
                        == "interrupt"
                    )
                    if interrupted:
                        recording.with_suffix(".interrupted").write_text("received")
                continue
            send(frame)
            if frame.get("type") != "control_request":
                continue
            while True:
                reply = read_message()
                handled = control(reply, version)
                if handled is not None:
                    interrupted |= handled == "interrupt"
                    continue
                response = reply.get("response")
                if not isinstance(response, dict):
                    raise ValueError("expected hook response")
                fields = cast("dict[str, object]", response)
                if (
                    fields.get("request_id") != frame.get("request_id")
                    or fields.get("subtype") != "success"
                ):
                    raise ValueError("unexpected hook response")
                with recording.with_suffix(".decisions").open("a") as decisions:
                    decisions.write(json.dumps(fields.get("response")) + "\n")
                break


if __name__ == "__main__":
    play(Path(os.environ["KORA_CLAUDE_REPLAY_RECORDING"]), sys.argv[1])
