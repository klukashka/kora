import asyncio
import os
import re
import secrets
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Final

from pydantic import AfterValidator, Field, StrictStr

from kora.ir.json import JsonObject, parse_json_object
from kora.protocols.common import BoundaryModel, ProtocolContext
from kora.protocols.deadlines import ExecutionDeadline
from kora.protocols.errors import KoraProtocolError
from kora.protocols.evidence import TerminationReceipt
from kora.protocols.identity import Handle
from kora.protocols.runtime import RuntimeCapabilities, RuntimeStatus
from kora.protocols.vcs import WorkspaceTarget, printable_path
from kora.runner.filesystem import (
    DirectoryAnchor,
    FilesystemError,
    open_local_directory,
)
from kora.runner.identity import IdentityReader, NativeIdentityReader
from kora.runner.registry import (
    RUNTIME_NAME,
    Applied,
    AttemptKey,
    Entry,
    Registry,
    Retired,
    Transition,
    TransitionExecutor,
    bind_workspace,
    lineage_of,
    lookup_cell,
    open_domain,
    refusal,
    retire_cell,
    status_cell,
)

STATE_ROOT_LENGTH: Final[int] = 4052
ATTEMPT: Final[re.Pattern[str]] = re.compile(r"[\x21-\x7e]{1,256}")
REJECTIONS: Final[dict[str, str]] = {
    "state_root_unsafe": "the local state root is missing or unsafe",
    "attempt_invalid": "the attempt id is not valid for this call",
    "handle_invalid": "the handle does not belong to this runtime",
}

StateRoot = Annotated[
    StrictStr,
    Field(min_length=1, max_length=STATE_ROOT_LENGTH),
    AfterValidator(printable_path),
]


class LocalRuntimeConfiguration(BoundaryModel):
    state_root: StateRoot | None


CONFIG_SCHEMA: Final[JsonObject] = parse_json_object(
    LocalRuntimeConfiguration.model_json_schema()
)


def rejection(code: str) -> KoraProtocolError:
    return KoraProtocolError(code, REJECTIONS[code], False, {})


def default_state_root(
    platform: str, environment: Mapping[str, str], home: Path
) -> Path:
    if platform == "darwin":
        return home / "Library" / "Application Support" / "kora" / "local"
    configured = Path(environment.get("XDG_STATE_HOME", ""))
    base = configured if configured.is_absolute() else home / ".local" / "state"
    return base / "kora" / "local"


def resolved_root(configuration: LocalRuntimeConfiguration) -> Path:
    if configuration.state_root is not None:
        return Path(configuration.state_root)
    default = default_state_root(sys.platform, os.environ, Path.home())
    LocalRuntimeConfiguration(state_root=str(default))
    return default


def anchored_root(
    configuration: LocalRuntimeConfiguration,
) -> tuple[Path, DirectoryAnchor] | None:
    try:
        path = resolved_root(configuration)
        return path, open_local_directory(path)
    except (OSError, FilesystemError, ValueError, RuntimeError):
        return None


def release_root(located: tuple[Path, DirectoryAnchor] | None) -> None:
    if located is not None:
        located[1].close()


def expired(deadline: ExecutionDeadline | None, now: datetime) -> bool:
    return deadline is not None and now >= deadline.at - deadline.clock_uncertainty


def random_identifier() -> str:
    return secrets.token_hex(16)


class LocalRuntime:
    name: str = RUNTIME_NAME
    config_schema: JsonObject = CONFIG_SCHEMA
    execution_domain_id: str

    def __init__(
        self,
        root_path: Path,
        root: DirectoryAnchor,
        registry: Registry,
        executor: TransitionExecutor,
        new_identifier: Callable[[], str],
    ) -> None:
        self.execution_domain_id = registry.execution_domain_id
        self.registry = registry
        self.executor = executor
        self._root_path = root_path
        self._root = root
        self._new_identifier = new_identifier
        self._closing: asyncio.Task[None] | None = None

    @classmethod
    async def open(
        cls,
        configuration: LocalRuntimeConfiguration,
        ctx: ProtocolContext,
        *,
        identities: IdentityReader | None = None,
        new_identifier: Callable[[], str] = random_identifier,
    ) -> "LocalRuntime":
        executor = TransitionExecutor()
        try:
            reader = (
                identities
                if identities is not None
                else await executor.run(NativeIdentityReader)
            )
            lineage = await executor.run(lambda: lineage_of(reader))
            located = await executor.run(
                lambda: anchored_root(configuration), release_root
            )
            if located is None:
                raise rejection("state_root_unsafe")
            path, root = located
            try:
                domain, directory = await executor.locked(
                    lambda: open_domain(root, lineage, new_identifier, new_identifier),
                    ctx,
                    "open",
                    lambda opened: opened[1].close(),
                )
            except BaseException:
                root.close()
                raise
        except BaseException:
            await executor.close()
            raise
        registry = Registry(directory, domain.execution_domain_id, new_identifier)
        return cls(path, root, registry, executor, new_identifier)

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            supports_resume=False,
            portable_handles=False,
            enforces_network_policy=False,
            isolates_tool_secrets=False,
            credential_proxy=False,
            protected_execution=False,
            plan_executors=frozenset({"runner", "worker_host"}),
            max_concurrent=None,
            default_stall_after=timedelta(minutes=15),
        )

    def key(self, attempt_id: str, ctx: ProtocolContext) -> AttemptKey:
        if not ATTEMPT.fullmatch(attempt_id) or ctx.attempt_id not in (
            None,
            attempt_id,
        ):
            raise rejection("attempt_invalid")
        return self.registry.key(attempt_id)

    def key_of(self, handle: Handle, ctx: ProtocolContext) -> AttemptKey:
        key = self.key(handle.attempt_id, ctx)
        if handle != key.handle():
            raise rejection("handle_invalid")
        return key

    async def transition(
        self,
        key: AttemptKey,
        decide: Callable[[Entry | None], Transition],
        ctx: ProtocolContext,
        operation: str,
    ) -> Applied:
        applied = await self.executor.locked(
            lambda: self.registry.try_transition(key, decide), ctx, operation
        )
        if applied.transition.refusal is not None:
            raise refusal(applied.transition.refusal)
        return applied

    async def entry(self, key: AttemptKey) -> Entry | None:
        return await self.executor.run(lambda: self.registry.entry(key))

    async def workspace_target(
        self, attempt_id: str, ctx: ProtocolContext
    ) -> WorkspaceTarget:
        key = self.key(attempt_id, ctx)
        deadline = ctx.deadline
        await self.transition(
            key,
            lambda entry: bind_workspace(
                entry, deadline, expired(deadline, ctx.clock.now())
            ),
            ctx,
            "workspace_target",
        )
        target = str(self._root_path / "workspaces" / key.name)
        return WorkspaceTarget(root=target, host_path=target)

    async def lookup(self, attempt_id: str, ctx: ProtocolContext) -> Handle | None:
        key = self.key(attempt_id, ctx)
        return lookup_cell(await self.entry(key), key)

    async def status(self, handle: Handle, ctx: ProtocolContext) -> RuntimeStatus:
        key = self.key_of(handle, ctx)
        return status_cell(await self.entry(key), ctx.clock.now())

    async def retire(
        self, attempt_id: str, grace: timedelta, ctx: ProtocolContext
    ) -> TerminationReceipt:
        key = self.key(attempt_id, ctx)
        retirement_id = self._new_identifier()
        stopped_at = ctx.clock.now()
        applied = await self.transition(
            key,
            lambda entry: retire_cell(entry, key, retirement_id, stopped_at),
            ctx,
            "retire",
        )
        head = applied.head
        if head is None or not isinstance(head.entry, Retired):
            raise refusal("lineage_unavailable")
        return head.entry.receipt

    async def close(self) -> None:
        if self._closing is None:
            self.executor.closed = True
            self._closing = asyncio.create_task(self._release())
        await asyncio.shield(self._closing)

    async def _release(self) -> None:
        try:
            await self.executor.close()
        finally:
            self.registry.directory.close()
            self._root.close()
