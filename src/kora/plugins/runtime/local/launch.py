import asyncio
import hashlib
import hmac
import os
import secrets
import signal
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter, ValidationError

from kora.observability import CATALOG, ComponentLogger, EventSpec, FieldKind, Level
from kora.plugins.runtime.local.channel import (
    PRE_ATTACH_BYTES,
    FrameType,
    LaunchCapability,
    LaunchExit,
    LaunchFailure,
    LaunchSignal,
    LaunchSpecEnvelope,
    LaunchSpecRequest,
    LaunchStarted,
)
from kora.plugins.runtime.local.environment import candidate_environment
from kora.plugins.runtime.local.reaper import AdoptedReaper
from kora.protocols.harness import LaunchEnd, LaunchInputs, Placement
from kora.protocols.identity import ProcessIdentity
from kora.protocols.lifetime import EnvironmentVariable
from kora.protocols.secret_value import SecretValue
from kora.protocols.time import Clock
from kora.runner.agent import refused
from kora.runner.channels import BoundedChannel, ChannelError, cancel_task, wait_future
from kora.runner.filesystem import (
    DirectoryAnchor,
    FilesystemError,
    ReleaseState,
    open_private_directory,
    release_tree,
)
from kora.runner.identity import NativeIdentityReader
from kora.runner.native_io import observed

COMPONENT: Final[str] = CATALOG.register_component("runtime.local.launcher")
ENDED: Final[EventSpec] = CATALOG.register(
    EventSpec(
        "runtime.local.launcher.ended",
        COMPONENT,
        Level.INFO,
        "the harness launch ended",
        {
            "launch_id": FieldKind.STRING,
            "cause": FieldKind.STRING,
            "exit_code": FieldKind.INTEGER,
            "signal": FieldKind.INTEGER,
        },
    )
)
REPORT: Final[
    TypeAdapter[LaunchStarted | LaunchExit | LaunchSignal | LaunchFailure]
] = TypeAdapter(LaunchStarted | LaunchExit | LaunchSignal | LaunchFailure)


@dataclass(frozen=True)
class ExpectedBinary:
    path: Path
    sha256: str
    size: int

    def matches(self) -> bool:
        descriptor = os.open(
            self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        )
        try:
            entry = os.fstat(descriptor)
            if not stat.S_ISREG(entry.st_mode) or entry.st_size != self.size:
                return False
            digest = hashlib.sha256()
            remaining = self.size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    return False
                digest.update(chunk)
                remaining -= len(chunk)
            return not os.read(descriptor, 1) and digest.hexdigest() == self.sha256
        finally:
            os.close(descriptor)


class LocalLaunch:
    def __init__(
        self,
        owner: "LocalLaunches",
        launch_id: str,
        capability: str,
        spec: LaunchSpecEnvelope,
    ) -> None:
        self.owner = owner
        self.launch_id = launch_id
        self.cli_path = owner.path / launch_id / "claude"
        self.capability: str | None = hashlib.sha256(capability.encode()).hexdigest()
        self.spec: LaunchSpecEnvelope | None = spec
        self.identity: ProcessIdentity | None = None
        self.started = False
        self.ready = asyncio.Event()
        self.channel: BoundedChannel | None = None
        self.result: asyncio.Future[LaunchEnd] = (
            asyncio.get_running_loop().create_future()
        )
        self.deadline = owner.clock.monotonic_ns() + 10_000_000_000
        self.timer = asyncio.create_task(self.expire())

    async def expire(self) -> None:
        await self.owner.clock.sleep_until(self.deadline)
        if self.identity is None and not self.result.done():
            self.revoke()
            await self.kill()
            self.finish(LaunchEnd("launch_failed", None, None))

    def revoke(self) -> None:
        self.capability = None
        self.spec = None
        directory = open_private_directory(self.owner.directory, self.launch_id)
        try:
            try:
                os.unlink("capability", dir_fd=directory.descriptor)
            except FileNotFoundError:
                return
        finally:
            directory.close()

    async def kill(self) -> None:
        identities = (
            (self.identity,)
            if self.identity is not None
            else self.owner.identities.launcher_children(str(self.cli_path))
        )
        for identity in identities:
            current = self.owner.identities.read(identity.pid).process
            if (
                current is None
                or current.dead
                or current.identity.start_time != identity.start_time
            ):
                continue
            try:
                if self.started:
                    if current.process_group_id == identity.pid:
                        os.killpg(identity.pid, signal.SIGKILL)
                else:
                    os.kill(identity.pid, signal.SIGTERM)
            except ProcessLookupError:
                continue

    async def ended(self) -> LaunchEnd:
        return await asyncio.shield(self.result)

    def finish(self, outcome: LaunchEnd) -> None:
        if self.result.done():
            return
        self.revoke()
        progress = release_tree(self.owner.directory, self.launch_id)
        if progress.state is not ReleaseState.COMPLETE:
            raise FilesystemError("launch_cleanup_incomplete")
        fields: dict[str, str | int] = {
            "launch_id": self.launch_id,
            "cause": str(outcome.cause),
        }
        if outcome.exit_code is not None:
            fields["exit_code"] = outcome.exit_code
        if outcome.signal is not None:
            fields["signal"] = outcome.signal
        self.owner.log.emit(ENDED, **fields)
        self.owner.pending.pop(self.launch_id)
        if self.identity is not None:
            self.owner.reaper.release(self.identity.pid)
            self.owner.held.discard(self.identity.pid)
        self.result.set_result(outcome)


class LocalLaunches:
    def __init__(
        self,
        placements: Mapping[Placement, Path],
        control: DirectoryAnchor,
        path: Path,
        socket_path: Path,
        binary: ExpectedBinary | None,
        token: SecretValue,
        clock: Clock,
        identities: NativeIdentityReader,
        log: ComponentLogger,
        reaper: AdoptedReaper,
    ) -> None:
        self.placements = placements
        self.path = path / "launch"
        self.socket_path = socket_path
        self.binary = binary
        self.token = token
        self.clock = clock
        self.identities = identities
        self.log = log
        self.reaper = reaper
        self.pending: dict[str, LocalLaunch] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.held: set[int] = set()
        self.closed = False
        self.closing: asyncio.Task[None] | None = None
        os.mkdir("launch", 0o700, dir_fd=control.descriptor)
        self.directory = open_private_directory(control, "launch")

    async def allocate(self, inputs: LaunchInputs) -> LocalLaunch:
        binary = self.binary
        if self.closed or binary is None or len(self.pending) >= 16:
            raise refused("launch_rejected")
        environment = candidate_environment(
            cwd=inputs.cwd,
            attempt_token=self.token,
            workload={},
            secrets={},
            harness_credentials={},
            adapter=inputs.environment,
        )
        launch_id = secrets.token_hex(16)
        capability = LaunchCapability(
            launch_id=launch_id,
            capability=secrets.token_hex(32),
            socket_path=str(self.socket_path),
        )
        spec = LaunchSpecEnvelope(
            request_id=launch_id,
            result="launch_spec",
            executable=str(binary.path),
            cwd=str(inputs.cwd),
            environment=tuple(
                EnvironmentVariable(name=name, value=value)
                for name, value in environment.items()
            ),
        )
        os.mkdir(launch_id, 0o700, dir_fd=self.directory.descriptor)
        directory = open_private_directory(self.directory, launch_id)
        try:
            for name, mode, content in (
                ("capability", 0o400, capability.model_dump_json().encode()),
                (
                    "claude",
                    0o500,
                    (
                        f"#!{sys.executable} -I\n"
                        "from kora.plugins.runtime.local.launcher import main\n"
                        "raise SystemExit(main())\n"
                    ).encode(),
                ),
            ):
                descriptor = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    mode,
                    dir_fd=directory.descriptor,
                )
                try:
                    with os.fdopen(descriptor, "wb", closefd=False) as stream:
                        stream.write(content)
                finally:
                    os.close(descriptor)
        except BaseException:
            release_tree(self.directory, launch_id)
            raise
        finally:
            directory.close()
        launch = LocalLaunch(self, launch_id, capability.capability, spec)
        self.pending[launch_id] = launch
        self.tasks.add(launch.timer)
        launch.timer.add_done_callback(self.tasks.discard)
        return launch

    async def serve(
        self, request: LaunchSpecRequest, peer: ProcessIdentity, channel: BoundedChannel
    ) -> None:
        launch = self.pending.get(request.launch_id)
        current = self.identities.read(peer.pid).process
        if (
            launch is None
            or launch.capability is None
            or self.clock.monotonic_ns() >= launch.deadline
            or current is None
            or current.dead
            or current.identity != peer
            or current.parent_pid != os.getpid()
            or current.session_id != os.getsid(0)
            or peer.leads_group
            or peer.pid in self.held
            or not hmac.compare_digest(
                launch.capability,
                hashlib.sha256(request.capability.encode()).hexdigest(),
            )
        ):
            raise refused("launch_rejected")
        spec = launch.spec
        launch.identity = peer
        self.reaper.register(peer.pid)
        self.held.add(peer.pid)
        outcome = LaunchEnd("launcher_died", None, None)
        launch.channel = channel
        try:
            launch.revoke()
            await cancel_task(launch.timer)
            binary = self.binary
            if binary is None:
                outcome = LaunchEnd("launch_failed", None, None)
                return
            verification = asyncio.create_task(asyncio.to_thread(binary.matches))
            cancelled = await observed(verification)
            matches = verification.result()
            if cancelled:
                raise asyncio.CancelledError
            if self.closed:
                return
            if not matches:
                outcome = LaunchEnd("integrity_mismatch", None, None)
                return
            if spec is None:
                raise refused("launch_rejected")
            await channel.write_typed_frame(
                FrameType.JSON,
                spec.model_dump_json().encode(),
                self.clock.monotonic_ns() + 5_000_000_000,
            )
            spec = None
            while True:
                await channel.wait_readable()
                _, payload = await channel.read_typed_frame(
                    self.clock.monotonic_ns() + 5_000_000_000,
                    {FrameType.JSON.value: PRE_ATTACH_BYTES},
                )
                record = REPORT.validate_json(payload)
                if isinstance(record, LaunchStarted):
                    if launch.started or record.pgid != peer.pid:
                        break
                    launch.started = True
                    launch.ready.set()
                    continue
                if isinstance(record, LaunchFailure):
                    outcome = LaunchEnd("launch_failed", None, None)
                elif launch.started and isinstance(record, LaunchExit):
                    outcome = LaunchEnd(
                        "cli_exit_without_result", record.exit_code, None
                    )
                elif launch.started and isinstance(record, LaunchSignal):
                    outcome = LaunchEnd("launcher_died", None, record.signal)
                break
        except (ChannelError, ValidationError):
            outcome = LaunchEnd("launcher_died", None, None)
        except OSError:
            outcome = LaunchEnd("launch_failed", None, None)
        finally:
            channel.close()
            launch.channel = None
            launch.finish(outcome)

    async def close(self) -> None:
        if self.closing is None:
            self.closing = asyncio.create_task(self._release())
        cancelled = await observed(self.closing)
        self.closing.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _release(self) -> None:
        self.closed = True
        for launch in tuple(self.pending.values()):
            await launch.kill()
            if launch.identity is None:
                launch.finish(LaunchEnd("launch_failed", None, None))
            else:
                ready = asyncio.create_task(launch.ready.wait())
                deadline = self.clock.monotonic_ns() + 5_000_000_000
                expiry = asyncio.create_task(self.clock.sleep_until(deadline))
                try:
                    await asyncio.wait(
                        (ready, launch.result, expiry),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not ready.done() and not launch.result.done():
                        raise refused("kill_unconfirmed")
                    if not launch.result.done():
                        await launch.kill()
                    await wait_future(launch.result, deadline, self.clock)
                finally:
                    await cancel_task(ready)
                    await cancel_task(expiry)
        for task in tuple(self.tasks):
            await cancel_task(task)
        self.directory.close()
