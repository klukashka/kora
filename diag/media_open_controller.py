import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeAlias

from diag.media_open_reader import MediaReader, MediaSession, OpenRead
from tests.plugins.storage import pressure_images
from tests.plugins.storage.pressure_controller import PressureController
from tests.plugins.storage.pressure_holders import (
    Binding,
    HolderLimits,
    Observation,
    ObservationContext,
    Scan,
    binding_changed,
    capture_binding,
    revalidate,
    volume_matching,
)
from tests.plugins.storage.pressure_models import ImageStamp, PressureFailure

Arm: TypeAlias = Literal["O", "C", "control", "rest", "holder"]
Clearance: TypeAlias = Literal[
    "not_run",
    "none",
    "cleared",
    "persisted",
    "unavailable",
    "skipped_fixture_holder",
    "skipped_incomplete",
    "skipped_budget",
]
ObserverResult: TypeAlias = Literal["not_run", "complete", "incomplete", "unavailable"]
FreshCheck: TypeAlias = Literal["not_run", "equal", "differs", "absent", "raised"]
WINDOW_SECONDS = 5.0
BUDGET_OFFSET_SECONDS = 13.0
RELEASE_SECONDS = 30.0
POLL_SECONDS = 0.02
REST_SECONDS = 5.0
REST_HOLD_SECONDS = 1.0


@dataclass
class Trace:
    arm: Arm
    arm_point: bool = False
    device: str = ""
    clearance: Clearance = "not_run"
    first_open: OpenRead | Literal["none"] = "none"
    observer: ObserverResult = "not_run"
    fixture_holder: bool = False
    observer_seconds: float | None = None
    poll_sleeps: int = 0
    open_reads: int = 0
    loop_gaps: list[float] = field(default_factory=list[float])
    hold_seconds: float | None = None
    clearance_start: float | None = None
    discover_seconds: float | None = None
    fresh_check: FreshCheck = "not_run"
    pre_eject_open: OpenRead | Literal["none"] = "none"
    pre_helper_open: OpenRead | Literal["none"] = "none"
    last_read_at: float | None = None
    helper_spawn_at: float | None = None
    entry_stable: bool = True
    rest_probe: Literal["na", "pass", "fail"] = "na"


class ArmPointHook(Protocol):
    def __call__(self, attachment: pressure_images.Attachment) -> None: ...


@dataclass(frozen=True)
class Timing:
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


def observe_structured(
    binding: Binding, seconds: float, clock: Callable[[], float]
) -> tuple[ObserverResult, bool]:
    if sys.platform != "darwin" or seconds <= 0:
        return "unavailable", False
    observation = Observation(capture=binding.capture, detach_unique=None)
    context = ObservationContext(os.getpid(), None, os.path.realpath(sys.executable))
    limits = HolderLimits(seconds=seconds)
    scan = Scan(binding, context, limits, clock, clock() + seconds, observation)
    before, first = revalidate(binding)
    observation.matching = volume_matching(binding.capture, before, before)
    scan.run()
    after, last = revalidate(binding)
    capture = "unmounted" if binding.mountpoint is None else binding.capture
    changed = binding_changed(
        capture, before, after, tuple(zip(first, last, strict=True))
    )
    complete = (
        not changed
        and not observation.query_failure
        and observation.processes > 0
        and observation.visited == observation.processes
        and not observation.truncated
        and not binding.entities_truncated
    )
    holder = observation.holder_count > 0
    return ("complete" if complete else "incomplete"), holder


class ExperimentController(PressureController):
    def __init__(
        self,
        base: PressureController,
        arm: Arm,
        reader: MediaReader,
        trace: Trace,
        hook: ArmPointHook | None = None,
        timing: Timing | None = None,
    ) -> None:
        super().__init__(base.manifest, base.lease, base.observer)
        self.arm = arm
        self.reader = reader
        self.trace = trace
        self.hook = hook
        self.timing = timing if timing is not None else Timing()
        self.binding: Binding | None = None
        self.active: MediaSession | None = None

    def native_phase(
        self, phase: Literal["unmount", "eject"], owned: pressure_images.Attachment
    ) -> None:
        if phase == "eject":
            self.trace.helper_spawn_at = self.timing.clock()
        super().native_phase(phase, owned)

    def release_native(self, expected: pressure_images.Attachment) -> None:
        if sys.platform != "darwin" or self.native_deadline is None:
            raise PressureFailure("native_platform")
        self.binding = capture_binding(
            self.directory / "mounted",
            expected.volume,
            expected.entities,
            self.manifest.directory_stamp.device,
        )
        self.settle_native()
        stamp = self.manifest.image_stamp
        if stamp is None:
            raise PressureFailure("native_image_missing")
        owned = pressure_images.discover(self.directory, stamp, self.native_inventory)
        if owned != expected or owned is None:
            raise PressureFailure("attachment_identity")
        if owned.mounted:
            self.native_phase("unmount", owned)
            checked = pressure_images.discover(
                self.directory, stamp, self.native_inventory
            )
            if (
                checked is None
                or checked.device != owned.device
                or checked.entities != owned.entities
                or checked.mounted
            ):
                raise PressureFailure("attachment_identity")
            owned = checked
        self.trace.arm_point = True
        self.trace.device = owned.device
        if self.hook is not None:
            self.hook(owned)
        try:
            if self.arm == "rest":
                self.rest_probe(owned.device)
            if self.arm != "O":
                self.clearance(owned.device)
            self.fresh_check(stamp, owned)
            session = (
                self.active
                if self.active is not None
                else self.open_session(owned.device)
            )
            if self.arm == "O":
                self.trace.pre_eject_open = self.read(session, counted=False)
                self.trace.last_read_at = self.timing.clock()
            else:
                self.trace.pre_helper_open = self.read(session, counted=False)
        finally:
            self.close_active()
        self.native_phase("eject", owned)
        if (
            pressure_images.discover(self.directory, stamp, self.native_inventory)
            is not None
        ):
            raise PressureFailure("attachment_still_present")

    def open_session(self, device: str) -> MediaSession:
        self.close_active()
        session, _ = self.reader.session(device)
        self.active = session
        return session

    def close_active(self) -> None:
        session = self.active
        if session is None:
            return
        self.active = None
        self.trace.entry_stable = self.trace.entry_stable and session.stable
        session.close()

    def read(self, session: MediaSession, *, counted: bool = True) -> OpenRead:
        value = session.read()
        if counted:
            self.trace.open_reads += 1
        if value == "unavailable":
            self.trace.entry_stable = False
        return value

    def fresh_check(self, stamp: ImageStamp, owned: pressure_images.Attachment) -> None:
        started = self.timing.clock()
        try:
            fresh = pressure_images.discover(
                self.directory, stamp, self.native_inventory
            )
        except PressureFailure as failure:
            if failure.code in {"attachment_identity", "mount_identity"}:
                self.trace.fresh_check = "raised"
            raise
        finally:
            self.trace.discover_seconds = self.timing.clock() - started
        if fresh is None:
            self.trace.fresh_check = "absent"
            raise PressureFailure("attachment_identity")
        if fresh != owned:
            self.trace.fresh_check = "differs"
            raise PressureFailure("attachment_identity")
        self.trace.fresh_check = "equal"

    def window(self) -> tuple[float, float, float]:
        if self.native_deadline is None:
            raise PressureFailure("native_platform")
        origin = self.native_deadline - RELEASE_SECONDS
        start = self.timing.clock()
        return (
            origin,
            start,
            min(start + WINDOW_SECONDS, origin + BUDGET_OFFSET_SECONDS),
        )

    def rest_probe(self, device: str) -> None:
        session = self.open_session(device)
        clock = self.timing.clock
        try:
            deadline = clock() + REST_SECONDS
            value = session.read()
            while value == "true" and clock() < deadline:
                self.timing.sleep(min(POLL_SECONDS, max(0.0, deadline - clock())))
                value = session.read()
            if value != "false":
                self.trace.rest_probe = "fail"
                return
            hold_until = clock() + REST_HOLD_SECONDS
            while clock() < hold_until:
                self.timing.sleep(min(POLL_SECONDS, max(0.0, hold_until - clock())))
                if session.read() != "false":
                    self.trace.rest_probe = "fail"
                    return
            self.trace.rest_probe = "pass"
        finally:
            self.close_active()

    def clearance(self, device: str) -> None:
        clock = self.timing.clock
        origin, start, end = self.window()
        self.trace.clearance_start = start - origin
        if start >= origin + BUDGET_OFFSET_SECONDS:
            self.trace.clearance = "skipped_budget"
            return
        session = self.open_session(device)
        first = self.read(session)
        first_at = clock()
        self.trace.last_read_at = first_at
        self.trace.first_open = first
        if first == "unavailable":
            self.trace.clearance = "unavailable"
            return
        if first == "false":
            self.trace.clearance = "none"
            return
        if self.binding is None:
            self.trace.observer = "unavailable"
            self.trace.clearance = "skipped_incomplete"
            return
        observed_at = clock()
        result, holder = observe_structured(
            self.binding, max(0.0, end - observed_at), clock
        )
        self.trace.observer_seconds = clock() - observed_at
        self.trace.observer = result
        self.trace.fixture_holder = holder
        if holder:
            self.trace.clearance = "skipped_fixture_holder"
            return
        if result != "complete":
            self.trace.clearance = "skipped_incomplete"
            return
        previous: float | None = None
        while True:
            now = clock()
            if now >= end:
                self.trace.clearance = "persisted"
                return
            self.timing.sleep(min(POLL_SECONDS, end - now))
            self.trace.poll_sleeps += 1
            value = self.read(session)
            read_at = clock()
            self.trace.last_read_at = read_at
            if previous is not None:
                self.trace.loop_gaps.append(read_at - previous)
            previous = read_at
            if value == "unavailable":
                self.trace.clearance = "unavailable"
                return
            if value == "false":
                self.trace.hold_seconds = read_at - first_at
                self.trace.clearance = "cleared"
                return
