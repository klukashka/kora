import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.architecture.test_measurement_job_routing import (
    SCAFFOLD_SHARDS,
    steps,
    table,
    workflow_jobs,
)
from tests.support.ordinary_inventory import GROUPS

FIRST_MODULES = (
    "tests/conformance/test_conformance_cases_detect_broken_providers.py",
    "tests/conformance/test_lifetime_helper_phases.py",
    "tests/conformance/test_lifetime_measurement_cohorts.py",
    "tests/conformance/test_lifetime_parent_exit.py",
    "tests/conformance/test_lifetime_pending_helpers.py",
    "tests/conformance/test_lifetime_resume.py",
    "tests/conformance/test_lifetime_rpc_concurrency.py",
    "tests/conformance/test_lifetime_services.py",
    "tests/conformance/test_local_process_lifetime.py",
    "tests/conformance/test_parent_lifetime.py",
    "tests/conformance/test_recovered_process_lifetime.py",
)
COLLECTIONS = (
    "full.collection",
    "first.collection",
    "second.collection",
    "measurements.collection",
    "database.collection",
    "other.collection",
)
SHARD_CHILDREN = {
    "conformance": ("first", "second"),
    "remainder": ("measurements", "database", "other"),
}
GROUP_ARGUMENTS = {
    "full": "tests",
    "first": '"${first[@]}"',
    "second": '"${second[@]}"',
    "measurements": '"${measurements[@]}"',
    "database": '"${database[@]}"',
    "other": '"${other[@]}"',
}


def shard_evidence(shard: str) -> tuple[str, ...]:
    children = SHARD_CHILDREN[shard]
    return (
        *COLLECTIONS,
        "inventory.json",
        *(f"{child}.log" for child in children),
        *(f"{child}.xml" for child in children),
    )


def ordinary_step() -> dict[str, object]:
    return steps(table(workflow_jobs()["scaffold-shard"]))["ordinary_tests"]


def ordinary_shell() -> str:
    return str(ordinary_step()["run"])


def shard_branch(shard: str) -> str:
    command = ordinary_shell()
    launches = command.partition('pids=()\ncase "$KORA_ORDINARY_SHARD" in\n')[2]
    return launches.partition(f"  {shard})\n")[2].partition("    ;;\n")[0]


def evidence_validation() -> str:
    marker = 'for name in "${evidence[@]}"; do\n  test -f'
    remainder = ordinary_shell().partition(marker)[2]
    return marker + remainder.partition("done\n")[0] + "done\n"


def test_ordinary_ci_collects_exact_groups_and_uploads_only_evidence() -> None:
    selected = steps(table(workflow_jobs()["scaffold-shard"]))
    command = ordinary_shell()

    first = command.partition("first=(\n")[2].partition(")\n")[0].split()

    assert command.startswith(
        'case "$KORA_ORDINARY_SHARD" in\n'
        "  conformance|remainder) ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
        'KORA_ORDINARY_OUTPUT="$(mktemp -d "$RUNNER_TEMP/kora-ordinary.XXXXXX")"\n'
    )
    assert "${{" not in command
    assert table(selected["ordinary_tests"]["env"]) == {
        "KORA_ORDINARY_SHARD": "${{ matrix.shard }}"
    }
    assert tuple(first) == FIRST_MODULES
    assert (
        'second=(tests/conformance)\nfor path in "${first[@]}"; do\n'
        '  second+=("--ignore=$path")\ndone\n'
        "measurements=(tests/measurements)\n"
        "database=(tests/db)\n"
        "other=(tests --ignore=tests/conformance --ignore=tests/db "
        "--ignore=tests/measurements)\n"
    ) in command
    collections = [
        command.index(
            "uv run --no-sync pytest --collect-only -q -o "
            f'cache_dir="$KORA_ORDINARY_OUTPUT/cache-{group}" {arguments} '
            f'> "$KORA_ORDINARY_OUTPUT/{group}.collection"\n'
        )
        for group, arguments in GROUP_ARGUMENTS.items()
    ]
    assert collections == sorted(collections)
    assert (
        collections[-1]
        < command.index("python -m tests.support.ordinary_inventory")
        < command.index("pids=()")
    )
    for shard, children in SHARD_CHILDREN.items():
        branch = shard_branch(shard)
        launches = "".join(
            "    uv run --no-sync python -m tests.support.ordinary_pytest -q -o "
            f'cache_dir="$KORA_ORDINARY_OUTPUT/cache-{child}" '
            f'--basetemp="$KORA_ORDINARY_OUTPUT/tmp-{child}" '
            f'--junitxml="$KORA_ORDINARY_OUTPUT/{child}.xml" '
            f"{GROUP_ARGUMENTS[child]} "
            f'> "$KORA_ORDINARY_OUTPUT/{child}.log" 2>&1 &\n'
            '    pids+=("$!")\n'
            for child in children
        )
        assert branch == (
            launches + "    evidence=(" + " ".join(shard_evidence(shard)) + ")\n"
        )
    assert command.count('pids+=("$!")') == 5
    assert "    ;;\n  *) exit 1 ;;\nesac\nresult=0\n" in command
    assert '  if wait "$pid"; then\n    :\n  else\n    result=1\n  fi' in command
    assert (
        command.index("esac\nresult=0\n")
        < command.index('for pid in "${pids[@]}"')
        < command.index('for name in "${evidence[@]}"')
        < command.index('exit "$result"')
    )
    assert (
        'for name in "${evidence[@]}"; do\n'
        '  test -f "$KORA_ORDINARY_OUTPUT/$name"\n'
        '  test ! -L "$KORA_ORDINARY_OUTPUT/$name"\n'
        '  test -s "$KORA_ORDINARY_OUTPUT/$name"\n'
        "done\n"
        'for name in "${evidence[@]}"; do\n'
        '  case "$name" in\n'
        '    inventory.json|*.log) cat "$KORA_ORDINARY_OUTPUT/$name" ;;\n'
        "  esac\n"
        "done\n"
        'exit "$result"\n'
    ) in command
    assert set(selected["ordinary_tests"]) == {"name", "id", "env", "run"}
    upload = selected["ordinary_upload"]
    assert upload["if"] == "${{ always() }}"
    assert upload["uses"] == (
        "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f"
    )
    uploaded = (
        *COLLECTIONS,
        "inventory.json",
        *(f"{child}.log" for children in SHARD_CHILDREN.values() for child in children),
        *(f"{child}.xml" for children in SHARD_CHILDREN.values() for child in children),
    )
    assert table(upload["with"]) == {
        "name": (
            "ordinary-tests-${{ matrix.os }}-python-${{ matrix.python }}"
            "-${{ matrix.shard }}"
        ),
        "path": "".join(
            "${{ env.KORA_ORDINARY_OUTPUT }}/" + name + "\n" for name in uploaded
        ),
        "if-no-files-found": "error",
    }
    gate = selected["required_ordinary"]
    assert gate["if"] == "${{ always() }}"
    assert table(gate["env"]) == {
        "ORDINARY_RESULT": "${{ steps.ordinary_tests.outcome }}",
        "ORDINARY_UPLOAD_RESULT": "${{ steps.ordinary_upload.outcome }}",
    }
    assert gate["run"] == (
        'test "$ORDINARY_RESULT" = success\ntest "$ORDINARY_UPLOAD_RESULT" = success\n'
    )
    assert list(selected) == ["ordinary_tests", "ordinary_upload", "required_ordinary"]


@pytest.mark.parametrize("shard", ["conformance", "remainder", "", "other", "all"])
def test_ordinary_shell_refuses_unknown_shard_before_collection(
    tmp_path: Path, shard: str
) -> None:
    opening = ordinary_shell().partition("esac\n")[0] + "esac\n"

    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", opening + "printf started\n"],
        cwd=tmp_path,
        env={**os.environ, "KORA_ORDINARY_SHARD": shard},
        capture_output=True,
        check=False,
        timeout=5,
    )

    valid = shard in SHARD_CHILDREN
    assert result.returncode == (0 if valid else 1)
    assert (result.stdout == b"started") is valid


@pytest.mark.parametrize("failed_child", [None, 0, 1, 2])
def test_ordinary_shell_joins_all_owned_children_after_failure(
    tmp_path: Path, failed_child: int | None
) -> None:
    join = ordinary_shell().partition("result=0\n")[2].partition("for name in ")[0]
    launches = ["pids=()"]
    for index in range(3):
        exit_code = 1 if index == failed_child else 0
        launches.append(f'(printf complete > "{index}"; exit {exit_code}) &')
        launches.append('pids+=("$!")')
    script = "\n".join(launches) + "\nresult=0\n" + join + 'exit "$result"\n'

    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == (0 if failed_child is None else 1)
    assert all((tmp_path / str(index)).read_text() == "complete" for index in range(3))


def test_ordinary_background_pytest_preserves_interruptible_children(
    tmp_path: Path,
) -> None:
    source = tmp_path / "test_signal.py"
    source.write_text(
        "import signal\n\n"
        "def test_interruptible_child() -> None:\n"
        "    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler\n"
    )
    script = (
        '"$KORA_TEST_PYTHON" -m tests.support.ordinary_pytest -q '
        '"$KORA_TEST_SOURCE" &\n'
        'pid="$!"\nwait "$pid"\n'
    )

    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", script],
        env={
            **os.environ,
            "KORA_TEST_PYTHON": sys.executable,
            "KORA_TEST_SOURCE": str(source),
        },
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert b"1 passed" in result.stdout


@pytest.mark.parametrize("shard", list(SHARD_CHILDREN))
@pytest.mark.parametrize("invalid", [None, "missing", "empty", "symlink"])
def test_ordinary_shell_requires_all_regular_nonempty_evidence(
    tmp_path: Path, shard: str, invalid: str | None
) -> None:
    evidence = shard_evidence(shard)
    for name in evidence:
        (tmp_path / name).write_text("complete")
    target = tmp_path / evidence[-1]
    if invalid == "missing":
        target.unlink()
    elif invalid == "empty":
        target.write_text("")
    elif invalid == "symlink":
        target.unlink()
        target.symlink_to(tmp_path / "inventory.json")
    assignment = shard_branch(shard).rpartition("    evidence=")[2]

    result = subprocess.run(
        [
            "/bin/bash",
            "-e",
            "-o",
            "pipefail",
            "-c",
            "evidence=" + assignment + evidence_validation(),
        ],
        env={**os.environ, "KORA_ORDINARY_OUTPUT": str(tmp_path)},
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert (result.returncode == 0) is (invalid is None)


@pytest.mark.parametrize("field", ["ORDINARY_RESULT", "ORDINARY_UPLOAD_RESULT"])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled", "skipped", ""])
def test_ordinary_admission_rejects_every_incomplete_outcome(
    field: str, outcome: str
) -> None:
    selected = steps(table(workflow_jobs()["scaffold-shard"]))
    environment = {
        **os.environ,
        "ORDINARY_RESULT": "success",
        "ORDINARY_UPLOAD_RESULT": "success",
        field: outcome,
    }

    result = subprocess.run(
        ["/bin/bash", "-e", "-c", str(selected["required_ordinary"]["run"])],
        env=environment,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert (result.returncode == 0) is (outcome == "success")


def test_shards_partition_every_inventory_group() -> None:
    children = [child for shard in SHARD_CHILDREN.values() for child in shard]

    assert tuple(SHARD_CHILDREN) == SCAFFOLD_SHARDS
    assert sorted(children) == sorted(GROUPS)
    assert len(children) == len(set(children))


@pytest.mark.parametrize("side", list(SHARD_CHILDREN))
@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "failed shard",
        "skipped shard",
        "cancelled shard",
        "unset shard",
        "failed download",
        "missing",
        "extra",
        "directory",
        "empty",
        "symlink",
        "unequal inventories",
    ],
)
def test_aggregate_admits_only_both_complete_matching_shards(
    tmp_path: Path, side: str, invalid: str | None
) -> None:
    for shard in SHARD_CHILDREN:
        directory = tmp_path / "shards" / shard
        directory.mkdir(parents=True)
        for name in shard_evidence(shard):
            (directory / name).write_text("complete")
    directory = tmp_path / "shards" / side
    own = shard_evidence(side)
    foreign = next(
        name
        for shard in SHARD_CHILDREN
        if shard != side
        for name in shard_evidence(shard)
        if name not in own
    )
    if invalid == "missing":
        (directory / own[-1]).unlink()
    elif invalid == "extra":
        (directory / foreign).write_text("complete")
    elif invalid == "directory":
        (directory / "tmp-other").mkdir()
    elif invalid == "empty":
        (directory / own[-2]).write_text("")
    elif invalid == "symlink":
        (directory / own[-1]).unlink()
        (directory / own[-1]).symlink_to(directory / own[-2])
    elif invalid == "unequal inventories":
        (directory / "inventory.json").write_text("different")
    admission = steps(table(workflow_jobs()["scaffold"]))["required_shards"]

    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", str(admission["run"])],
        cwd=tmp_path,
        env={
            **os.environ,
            "SHARD_RESULT": {
                "failed shard": "failure",
                "skipped shard": "skipped",
                "cancelled shard": "cancelled",
                "unset shard": "",
            }.get(str(invalid), "success"),
            "CONFORMANCE_DOWNLOAD_OUTCOME": (
                "failure"
                if invalid == "failed download" and side == "conformance"
                else "success"
            ),
            "REMAINDER_DOWNLOAD_OUTCOME": (
                "failure"
                if invalid == "failed download" and side == "remainder"
                else "success"
            ),
        },
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert (result.returncode == 0) is (invalid is None), result.stderr
