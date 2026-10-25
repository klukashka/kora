import json
from pathlib import Path

from tests.protocols import (
    test_provider_loading_imports_only_the_requested_entry_point as loading,
)
from tests.support.observability import run_python

SERVING: loading.Distribution = loading.Distribution(
    "kora-serving-fixture",
    "1.0",
    {"vcs.directory": "kora.plugins.vcs.fake:DirVcs"},
)


def test_serving_process_refuses_fake_providers(tmp_path: Path) -> None:
    first = tmp_path / "site-first"
    second = tmp_path / "site-second"
    first.mkdir()
    second.mkdir()
    loading.write_distribution(first, SERVING)
    log = tmp_path / "diagnostics.jsonl"

    completed = run_python(
        ["-I", str(loading.PROBE), "serving", str(first), str(second), str(log)], {}
    )

    assert completed.code == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["codes"] == {"vcs.directory": "provider_reserved"}
    assert result["reserved_modules"] == []
    assert result["drained"] is True
