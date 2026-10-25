import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from kora.conformance.capture import Capture
from kora.conformance.vcs import Checkout, Kept, pinned, publish_request, run
from kora.executor.failures import ExecutorError
from kora.ir.node import ErrorClass
from kora.protocols.vcs import PlanStep, PublishPlan
from tests.plugins.vcs.dirvcs_support import WORLDS, AnyWorld, make_dir_world
from tests.plugins.vcs.launchers import RecordingLauncher

LEAKED_VALUE: str = "kora-intermediate-secret-77d2"
FAULTS: Path = Path(__file__).with_name("decoder_faults.py")
History = Callable[[AnyWorld, Checkout], None]


def in_a_file(world: AnyWorld, checkout: Checkout) -> None:
    world.commit(checkout, {"notes.txt": f"{LEAKED_VALUE}\n"}, "intermediate")
    world.commit(checkout, {"notes.txt": "scrubbed\n"}, "scrubbed")


def in_a_message(world: AnyWorld, checkout: Checkout) -> None:
    world.commit(checkout, {"notes.txt": "one\n"}, f"carries {LEAKED_VALUE}")
    world.commit(checkout, {"notes.txt": "two\n"}, "clean")


def in_a_symlink_target(world: AnyWorld, checkout: Checkout) -> None:
    link = world.candidate(checkout) / "link"
    link.symlink_to(LEAKED_VALUE)
    world.commit(checkout, {}, "link")
    link.unlink()
    world.commit(checkout, {}, "unlink")


def in_a_path_name(world: AnyWorld, checkout: Checkout) -> None:
    candidate = world.candidate(checkout)
    shutil.copyfile(candidate / "a.txt", candidate / LEAKED_VALUE)
    world.commit(checkout, {}, "reused blob")
    world.commit(checkout, {LEAKED_VALUE: None}, "renamed away")


def secret_free(launcher: RecordingLauncher) -> bool:
    return not any(
        LEAKED_VALUE in " ".join((*spec.argv, *spec.env.values()))
        or LEAKED_VALUE.encode() in (spec.stdin or b"")
        for spec in launcher.specs
    )


@pytest.mark.parametrize(
    "history", [in_a_file, in_a_message, in_a_symlink_target, in_a_path_name]
)
@pytest.mark.parametrize("world_name", sorted(WORLDS))
def test_non_git_inspection_stream_scans_intermediate_history(
    tmp_path: Path, kora_capture: Capture, world_name: str, history: History
) -> None:
    launcher = RecordingLauncher()
    world = WORLDS[world_name](tmp_path, kora_capture, launcher)
    checkout = world.checkout()
    history(world, checkout)
    secret = world.capture.scope.admit(LEAKED_VALUE)

    with pytest.raises(ExecutorError) as raised:
        world.prepare(checkout, secrets=(secret,))

    assert raised.value.failure_class is ErrorClass.CREDENTIAL_LEAK
    assert raised.value.code == "secret_leak"
    assert world.remote_head() is None
    assert len(launcher.specs) > 0
    assert secret_free(launcher)


def faulty(plan: PublishPlan, mode: str) -> PublishPlan:
    decode = plan.inspect[0]
    helper = decode.argv.index("-I") + 1
    argv = ("python", "-I", str(FAULTS), mode, *decode.argv[helper:])
    replaced = PlanStep.model_validate({**decode.model_dump(), "argv": argv})
    return plan.model_copy(update={"inspect": (replaced, *plan.inspect[1:])})


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("truncate", "content_index_incomplete"),
        ("omit", "content_index_incomplete"),
        ("incomplete", "content_index_incomplete"),
        ("digest", "content_index_digest_mismatch"),
        ("corrupt", "content_digest_mismatch"),
    ],
)
def test_directory_decoder_faults_fail_before_authorization(
    tmp_path: Path, kora_capture: Capture, mode: str, code: str
) -> None:
    world = make_dir_world(tmp_path, kora_capture)
    checkout = world.checkout()
    world.commit(checkout, {"notes.txt": "draft\n"}, "intermediate")
    world.commit(checkout, {"notes.txt": "final\n"}, "final")
    context = world.context()
    remote = world.remote_fixture()
    request = publish_request(remote, checkout)
    plan = faulty(run(world.provider.publish_plan(request, context)), mode)
    workspace = checkout.workspace()
    exported = run(world.executor.export(plan, workspace, checkout.areas, context))
    sealed = run(world.executor.seal(exported, workspace, checkout.areas, context))

    with pytest.raises(ExecutorError) as raised:
        run(
            world.executor.prepare_and_inspect(
                plan,
                sealed,
                workspace,
                request,
                pinned(world.binding()),
                checkout.areas,
                (),
                (),
                Kept(),
                context,
            )
        )

    assert raised.value.failure_class is ErrorClass.ARTIFACT_INVALID
    assert raised.value.code == code
    assert world.remote_head() is None
