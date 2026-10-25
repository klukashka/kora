import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from kora.conformance.capture import Capture
from kora.conformance.vcs import Checkout
from kora.executor.digests import destination_digest, prepared_digest
from kora.executor.failures import ExecutorError
from kora.ir.json import JsonObject
from kora.plugins.vcs.fake import store
from kora.protocols.errors import KoraProtocolError
from tests.plugins.vcs.dirvcs_support import (
    AnyWorld,
    empty_store,
    make_dir_world,
    set_head,
    stored_revision,
)
from tests.plugins.vcs.git_support import PUBLISH_BRANCH, git, make_world, write

PREPARED: str = "prepared"


@dataclass(frozen=True)
class Scenario:
    world: AnyWorld
    checkout: Checkout
    decoy: Path
    state: str
    head: str | None


@dataclass(frozen=True)
class Observed:
    state: str
    head: str | None
    decoy_refs: str
    base: str | None
    branch: str | None
    redirect_kept: bool


def git_decoy(tmp_path: Path, capture: Capture) -> Scenario:
    world = make_world(tmp_path, capture)
    decoy = tmp_path / "decoy.git"
    git(world.home, "init", "-q", "--bare", str(decoy))
    checkout = world.checkout()
    candidate = world.candidate(checkout)
    for key, value in (
        ("remote.origin.url", str(decoy)),
        ("remote.origin.pushurl", str(decoy)),
        (f"url.{decoy}.insteadOf", str(world.remote)),
        (f"url.{decoy}.pushInsteadOf", str(world.remote)),
        ("branch.conformance-work.remote", "origin"),
        ("include.path", str(tmp_path / "included")),
    ):
        git(world.home, "config", key, value, cwd=candidate)
    (tmp_path / "included").write_text(f'[remote "origin"]\n\turl = {decoy}\n')
    (candidate / "redirect").symlink_to(decoy)
    world.commit(checkout, {"a.txt": "changed\n"}, "candidate change")
    return Scenario(world, checkout, decoy, "pushed", PREPARED)


def directory_decoy(tmp_path: Path, capture: Capture) -> Scenario:
    world = make_dir_world(tmp_path, capture)
    decoy = tmp_path / "decoy.dirvcs"
    empty_store(decoy)
    checkout = world.checkout()
    candidate = world.candidate(checkout)
    planted = {"path": str(decoy), "device": 0, "inode": 0}
    (candidate / ".kora" / "repository.json").write_text(json.dumps(planted))
    (candidate / "redirect").symlink_to(decoy)
    world.commit(checkout, {"a.txt": "changed\n"}, "candidate change")
    return Scenario(world, checkout, decoy, "pushed", PREPARED)


def directory_with_head(tmp_path: Path, capture: Capture, head: str | None) -> Scenario:
    world = make_dir_world(tmp_path, capture)
    existing = head or stored_revision(
        world.remote, {"other.txt": (store.REGULAR, b"other\n")}, world.base, "other"
    )
    set_head(world.remote, PUBLISH_BRANCH, existing)
    checkout = world.checkout()
    world.commit(checkout, {"a.txt": "descendant\n"}, "descendant")
    return Scenario(world, checkout, tmp_path / "absent", "conflict", existing)


def directory_different_head(tmp_path: Path, capture: Capture) -> Scenario:
    return directory_with_head(tmp_path, capture, None)


def directory_ancestor(tmp_path: Path, capture: Capture) -> Scenario:
    scenario = directory_with_head(tmp_path, capture, None)
    set_head(scenario.world.remote, PUBLISH_BRANCH, scenario.world.base)
    return Scenario(
        scenario.world,
        scenario.checkout,
        scenario.decoy,
        "conflict",
        scenario.world.base,
    )


def directory_replaced(tmp_path: Path, capture: Capture) -> Scenario:
    world = make_dir_world(tmp_path, capture)
    checkout = world.checkout()
    world.commit(checkout, {"a.txt": "moved\n"}, "moved")
    copied = tmp_path / "copied.dirvcs"
    shutil.copytree(world.remote, copied, symlinks=True)
    shutil.rmtree(world.remote)
    copied.rename(world.remote)
    return Scenario(
        world, checkout, tmp_path / "absent", "vcs_destination_changed", None
    )


LEGS: dict[str, Callable[[Path, Capture], Scenario]] = {
    "git-decoy": git_decoy,
    "directory-decoy": directory_decoy,
    "directory-different_head": directory_different_head,
    "directory-ancestor": directory_ancestor,
    "directory-replaced": directory_replaced,
}


def decoy_refs(scenario: Scenario) -> str:
    if (scenario.decoy / "heads").is_dir():
        return " ".join(
            sorted(path.name for path in (scenario.decoy / "heads").iterdir())
        )
    if scenario.decoy.is_dir():
        return git(scenario.world.home, f"--git-dir={scenario.decoy}", "for-each-ref")
    return ""


def attempt(scenario: Scenario) -> Observed:
    world = scenario.world
    try:
        prepared = world.prepare(scenario.checkout)
    except (ExecutorError, KoraProtocolError) as error:
        return Observed(
            error.code, world.remote_head(), decoy_refs(scenario), None, None, False
        )
    outcome = world.publish(prepared)
    publication = prepared.result.publication
    head = world.remote_head()
    return Observed(
        outcome.state,
        PREPARED if head == publication.commit else head,
        decoy_refs(scenario),
        publication.base_commit,
        None if outcome.ref is None else outcome.ref.branch,
        (world.candidate(scenario.checkout) / "redirect").is_symlink(),
    )


@pytest.mark.parametrize("leg", sorted(LEGS))
def test_publication_destination_is_trusted(
    tmp_path: Path, kora_capture: Capture, leg: str
) -> None:
    scenario = LEGS[leg](tmp_path, kora_capture)
    pushed = scenario.state == "pushed"
    prepared = scenario.state != "vcs_destination_changed"

    observed = attempt(scenario)

    assert observed.state == scenario.state
    assert observed.head == scenario.head
    assert observed.decoy_refs == ""
    assert observed.base == (scenario.world.base if prepared else None)
    assert observed.branch == (PUBLISH_BRANCH if pushed else None)
    assert observed.redirect_kept is pushed


def test_publication_never_fast_forwards_an_ancestor(
    tmp_path: Path, kora_capture: Capture
) -> None:
    world = make_world(tmp_path, kora_capture)
    git(
        world.home,
        f"--git-dir={world.remote}",
        "update-ref",
        f"refs/heads/{PUBLISH_BRANCH}",
        world.base,
    )
    checkout = world.checkout()
    world.commit(checkout, {"a.txt": "descendant\n"}, "descendant")
    prepared = world.prepare(checkout)

    outcome = world.publish(prepared)

    assert outcome.state == "conflict"
    assert outcome.ref is None
    assert world.remote_head() == world.base


def test_destination_side_hooks_are_trusted_operator_configuration(
    tmp_path: Path, kora_capture: Capture
) -> None:
    world = make_world(tmp_path, kora_capture)
    marker = tmp_path / "destination-hook-ran"
    hook = world.remote / "hooks" / "post-receive"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\n/usr/bin/touch {marker}\n")
    hook.chmod(0o755)
    checkout = world.checkout()
    write(world.candidate(checkout), {"hooked.txt": "hook\n"})
    prepared = world.prepare(checkout)

    outcome = world.publish(prepared)

    assert outcome.state == "pushed"
    assert marker.exists()


DESTINATION: tuple[str, str, str, str, str] = (
    "git",
    "/srv/remote.git",
    "repo",
    "kora/b",
    "c" * 40,
)


@pytest.mark.parametrize("changed", range(6))
def test_changing_any_destination_input_changes_the_digest(changed: int) -> None:
    transport: JsonObject = {"transport": "file", "binding_revision": 1}
    provider, remote, repo, branch, base = DESTINATION
    original = destination_digest(provider, remote, repo, branch, base, transport)
    altered = [
        f"{value}x" if index == changed else value
        for index, value in enumerate(DESTINATION)
    ]
    altered_transport: JsonObject = {
        **transport,
        "binding_revision": 2 if changed == 5 else 1,
    }

    digest = destination_digest(
        altered[0], altered[1], altered[2], altered[3], altered[4], altered_transport
    )

    assert digest != original


@pytest.mark.parametrize("changed", range(5))
def test_changing_any_prepared_input_changes_the_digest(changed: int) -> None:
    texts = ["a" * 40, "b" * 40, "s1", "p1"]
    raw = b"tree x\n"
    original = prepared_digest(texts[0], raw, texts[1], texts[2], texts[3])
    altered = [
        f"{text}y" if index == changed else text for index, text in enumerate(texts)
    ]
    altered_raw = raw + b"y" if changed == 4 else raw

    digest = prepared_digest(
        altered[0], altered_raw, altered[1], altered[2], altered[3]
    )

    assert digest != original
