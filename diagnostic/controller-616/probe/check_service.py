from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from process_owner import settle
from run_owned_service import (
    export_evidence,
    groups_absent,
    identify,
    parse_properties,
    successful,
)


def identity_controls() -> None:
    token = str(Path(tempfile.gettempdir()) / "kora616-service-owned")
    invocation = "1234567890abcdef1234567890abcdef"
    values = parse_properties(
        f"Description={token}\nInvocationID={invocation}\n".encode()
    )

    result = identify(values, token, invocation)

    if result != invocation:
        raise RuntimeError("identity_not_preserved")
    for data in (b"Description=x\nDescription=y\n", b"invalid\n"):
        try:
            parse_properties(data)
        except ValueError:
            continue
        raise RuntimeError("invalid_properties_accepted")
    for changed in (
        dict(values, Description="foreign"),
        dict(values, InvocationID=""),
        dict(values, InvocationID="f" * 32),
        dict(values, InvocationID="z" * 32),
    ):
        try:
            identify(changed, token, invocation)
        except RuntimeError:
            continue
        raise RuntimeError("foreign_identity_accepted")


def group_control() -> None:
    with tempfile.TemporaryDirectory(prefix="kora616-group-control-") as directory:
        root = Path(directory)
        process = subprocess.Popen(
            [sys.executable, "-c", "import os; r,w=os.pipe(); os.read(r,1)"],
            start_new_session=True,
        )
        (root / "groups").write_text(str(process.pid) + "\n")

        try:
            groups_absent(root)
        except RuntimeError as error:
            if str(error) != "owned_group_not_absent":
                raise
        else:
            raise RuntimeError("live_group_accepted")
        finally:
            settle(process)
        groups_absent(root)

        if process.returncode is None:
            raise RuntimeError("group_not_reaped")


def inherited_control() -> None:
    inherited = time.monotonic() - 10.0
    environment = dict(
        os.environ, KORA616_STARTED=str(inherited), PYTHONDONTWRITEBYTECODE="1"
    )
    code = "import json; from process_owner import STARTED; print(json.dumps(STARTED))"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parent,
        env=environment,
        capture_output=True,
        timeout=5.0,
        check=True,
    )

    value: object = json.loads(result.stdout)

    if value != inherited:
        raise RuntimeError("inherited_budget_reset")
    for invalid in (
        "nan",
        "inf",
        str(time.monotonic() + 100.0),
        str(time.monotonic() - 121.0),
    ):
        failed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).parent,
            env=dict(environment, KORA616_STARTED=invalid),
            capture_output=True,
            timeout=5.0,
            check=False,
        )
        if (
            failed.returncode == 0
            or b"invalid_inherited_probe_start" not in failed.stderr
        ):
            raise RuntimeError("invalid_start_accepted")


def result_controls() -> None:
    values = {"ExecMainCode": "1", "ExecMainStatus": "0", "Result": "success"}

    accepted = successful(values)
    rejected = [
        successful(dict(values, **changed))
        for changed in (
            {"ExecMainCode": "2"},
            {"ExecMainStatus": "1"},
            {"Result": "timeout"},
        )
    ]

    if not accepted or any(rejected):
        raise RuntimeError("invalid_exit_accepted")
    with tempfile.TemporaryDirectory(prefix="kora616-evidence-control-") as directory:
        root = Path(directory)
        archive = root / "archive"
        archive.mkdir()
        (root / "wrapper.json").write_bytes(b'{"cleanup":"unconfirmed"}')
        (root / "stderr").write_bytes(b"early_gate_failure")
        (root / "unrelated").write_bytes(b"not_exported")

        export_evidence(root, archive)

        if {path.name for path in archive.iterdir()} != {"wrapper.json", "stderr"}:
            raise RuntimeError("evidence_scope_changed")
        if (archive / "stderr").read_bytes() != b"early_gate_failure":
            raise RuntimeError("failure_evidence_lost")


def main() -> None:
    result_controls()
    identity_controls()
    group_control()
    inherited_control()
    sys.stdout.write(
        "Identity refusals, live/reaped group and inherited deadline controls passed; "
        "no system service executed\n"
    )


if __name__ == "__main__":
    main()
