import ctypes
import os
import sys
import time
from pathlib import Path
from typing import Protocol, cast

from tests.plugins.storage.pressure_controller import read_manifest, save
from tests.plugins.storage.pressure_holders import (
    SYSTEM_ROOTS,
    Native,
    number,
    process_comm,
)
from tests.plugins.storage.pressure_models import (
    EJECT_REFUSAL_LIMIT,
    NativeOutcome,
    NativeRequest,
    PressureFailure,
)
from tests.plugins.storage.pressure_native import process_token


class CFRange(ctypes.Structure):
    _fields_ = [("location", ctypes.c_long), ("length", ctypes.c_long)]


def private_text(cf: ctypes.CDLL, string: int) -> tuple[bytes, bool]:
    ref = ctypes.c_void_p
    ffi = ctypes.CFUNCTYPE
    length = ffi(ctypes.c_long, ref)(("CFStringGetLength", cf))
    copy = ffi(None, ref, CFRange, ref)(("CFStringGetCharacters", cf))
    units = cast(int, length(string))
    if units < 0:
        raise PressureFailure("native_text_invalid")
    count = min(units, 256)
    buffer = (ctypes.c_uint16 * count)()
    copy(string, CFRange(0, count), buffer)
    encoded = bytes(buffer).decode("utf-16-le", errors="replace").encode()
    if len(encoded) > 1024:
        raise PressureFailure("native_text_limit")
    return encoded, count < units


SYSTEM_PATHS = tuple(root.encode() for root in SYSTEM_ROOTS)
LAUNCHD = 1


def reported_process(value: int) -> int | None:
    return value if value > 0 else None


class DissenterPolicy(Protocol):
    def qualifies(self, pid: int) -> bool: ...


class ExternalSystemDaemon:
    def __init__(self, native: Native, excluded: frozenset[int]) -> None:
        self.native: Native = native
        self.excluded: frozenset[int] = excluded

    def qualifies(self, pid: int) -> bool:
        if pid <= LAUNCHD or pid in self.excluded:
            return False
        path, _ = self.native.query(pid, -3, 4096)
        information, _ = self.native.query(pid, 13, 64)
        return (
            path is not None
            and information is not None
            and number(information, "@I", 0) == pid
            and number(information, "@I", 4) == LAUNCHD
            and path.startswith(SYSTEM_PATHS)
        )


class NativeCall:
    def __init__(
        self, request: NativeRequest, policy: DissenterPolicy | None = None
    ) -> None:
        self.request: NativeRequest = request
        self.policy: DissenterPolicy | None = policy
        self.submitted: bool = False
        self.finished: bool = False
        self.invalid: bool = False
        self.cf_closed: bool = True
        self.refused: bool = False
        self.code: int = 0
        self.text: bytes = b""
        self.text_present: bool = False
        self.text_truncated: bool = False
        self.dissenter_pid: int | None = None
        self.dissenter_comm: str | None = None
        self.eject_refusals: int = 0

    def excluded(self) -> frozenset[int]:
        pids = {os.getpid(), self.request.controller.pid}
        if self.request.helper is not None:
            pids.add(self.request.helper.pid)
        return frozenset(pids)

    def repeatable(self, policy: DissenterPolicy) -> bool:
        return (
            self.request.phase == "eject"
            and self.refused
            and self.dissenter_pid is not None
            and self.eject_refusals < EJECT_REFUSAL_LIMIT
            and policy.qualifies(self.dissenter_pid)
        )

    def repeat(self) -> None:
        self.eject_refusals += 1
        self.finished = False
        self.refused = False
        self.code = 0
        self.text = b""
        self.text_present = False
        self.text_truncated = False
        self.dissenter_pid = None

    def perform(self) -> None:
        if sys.platform != "darwin":
            raise PressureFailure("native_platform")
        frameworks = "/System/Library/Frameworks/"
        cf = ctypes.CDLL(frameworks + "CoreFoundation.framework/CoreFoundation")
        da = ctypes.CDLL(frameworks + "DiskArbitration.framework/DiskArbitration")
        ref = ctypes.c_void_p
        callback_type = ctypes.CFUNCTYPE(None, ref, ref, ref)
        ffi = ctypes.CFUNCTYPE
        release = ffi(None, ref)(("CFRelease", cf))
        equal = ffi(ctypes.c_ubyte, ref, ref)(("CFEqual", cf))
        run_loop = ffi(ctypes.c_int, ref, ctypes.c_double, ctypes.c_ubyte)(
            ("CFRunLoopRunInMode", cf)
        )
        create_session = ffi(ref, ref)(("DASessionCreate", da))
        create_disk = ffi(ref, ref, ref, ctypes.c_char_p)(
            ("DADiskCreateFromBSDName", da)
        )
        schedule = ffi(None, ref, ref, ref)(("DASessionScheduleWithRunLoop", da))
        unschedule = ffi(None, ref, ref, ref)(("DASessionUnscheduleFromRunLoop", da))
        status = ffi(ctypes.c_uint32, ref)(("DADissenterGetStatus", da))
        status_string = ffi(ref, ref)(("DADissenterGetStatusString", da))
        process_id = ffi(ctypes.c_int, ref)(("DADissenterGetProcessID", da))
        native = Native()
        policy = (
            ExternalSystemDaemon(native, self.excluded())
            if self.policy is None
            else self.policy
        )
        operation = ffi(None, ref, ctypes.c_uint32, callback_type, ref)(
            ("DADiskUnmount" if self.request.phase == "unmount" else "DADiskEject", da)
        )
        mode = ref.in_dll(cf, "kCFRunLoopDefaultMode")
        loop = cast(int | None, ffi(ref)(("CFRunLoopGetCurrent", cf))())
        session: int | None = None
        disk: int | None = None
        scheduled = False

        def completed(
            actual: int | None, dissenter: int | None, context: int | None
        ) -> None:
            if (
                actual is None
                or not equal(actual, disk)
                or context is not None
                or self.finished
            ):
                self.invalid = True
                return
            self.refused = dissenter is not None
            self.code = 0 if dissenter is None else cast(int, status(dissenter))
            string = (
                None
                if dissenter is None
                else cast(int | None, status_string(dissenter))
            )
            self.dissenter_pid = (
                None
                if dissenter is None
                else reported_process(cast(int, process_id(dissenter)))
            )
            self.text_present = string is not None
            if string is not None:
                self.text, self.text_truncated = private_text(cf, string)
            self.finished = True

        callback = callback_type(completed)
        self.cf_closed = False
        try:
            session = cast(int | None, create_session(None))
            if session is None or loop is None or mode.value is None:
                raise PressureFailure("native_session_missing")
            target = (
                self.request.volume
                if self.request.phase == "unmount"
                else self.request.device
            )
            if target is None:
                raise PressureFailure("native_target_missing")
            disk = cast(int | None, create_disk(None, session, os.fsencode(target)))
            if disk is None:
                raise PressureFailure("native_disk_missing")
            schedule(session, loop, mode)
            scheduled = True
            deadline = min(self.request.deadline, time.monotonic() + 25)
            if time.monotonic() >= deadline:
                raise PressureFailure("native_admission_deadline")
            self.submitted = True
            operation(disk, 0, callback, None)
            while not self.invalid and time.monotonic() < deadline:
                if not self.finished:
                    run_loop(mode, max(0, deadline - time.monotonic()), 1)
                elif self.repeatable(policy):
                    self.repeat()
                    operation(disk, 0, callback, None)
                else:
                    break
            if self.invalid or not self.finished:
                raise PressureFailure("native_completion_unknown")
            if self.dissenter_pid is not None:
                self.dissenter_comm = process_comm(native, self.dissenter_pid)
        finally:
            if scheduled:
                unschedule(session, loop, mode)
            if disk is not None:
                release(disk)
            if session is not None:
                release(session)
            self.cf_closed = True

    def outcome(self) -> NativeOutcome | None:
        if (
            not self.cf_closed
            or self.request.helper is None
            or self.submitted
            and (not self.finished or self.invalid)
        ):
            return None
        return NativeOutcome(
            version=1,
            request_id=self.request.request_id,
            phase=self.request.phase,
            helper=self.request.helper,
            result="not_requested"
            if not self.submitted
            else "refused"
            if self.refused
            else "success",
            code=self.code if self.submitted else None,
            cf_closed=True,
            text_present=self.text_present,
            text_truncated=self.text_truncated,
            dissenter_pid=self.dissenter_pid,
            dissenter_comm=self.dissenter_comm,
            eject_refusals=self.eject_refusals,
        )


def main() -> None:
    os.umask(0o077)
    directory = Path(sys.argv[1])
    activation = sys.stdin.buffer.read(33)
    manifest = read_manifest(directory / "pressure-manifest.json")
    request = manifest.native
    token = process_token(os.getpid())
    if request is None or token is None or request.helper != token:
        raise PressureFailure("native_helper_unregistered")
    call = NativeCall(request)
    try:
        if activation != request.request_id.encode():
            raise PressureFailure("native_activation_invalid")
        if (
            manifest.controller != request.controller
            or process_token(request.controller.pid) != request.controller
            or manifest.nonce != request.nonce
            or manifest.image_stamp != request.image_stamp
        ):
            raise PressureFailure("native_authority_changed")
        remaining = request.deadline - time.monotonic()
        if not 0 < remaining <= 30:
            raise PressureFailure("native_admission_deadline")
        call.perform()
    finally:
        outcome = call.outcome()
        if outcome is not None:
            encoded = outcome.model_dump_json().encode()
            if len(encoded) > 8192:
                raise PressureFailure("native_outcome_limit")
            save(directory / "native-text.bin", call.text)
            save(directory / "native-result.json", encoded)


if __name__ == "__main__":
    main()
