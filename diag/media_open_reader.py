import ctypes
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

OpenRead: TypeAlias = Literal["true", "false", "unavailable"]
IOKIT_PATH = "/System/Library/Frameworks/IOKit.framework/IOKit"
FOUNDATION_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
UTF8_ENCODING = 0x08000100
MAIN_PORT_DEFAULT = 0
MATCH_LIMIT = 8


class Bindings:
    def __init__(self) -> None:
        iokit = ctypes.CDLL(IOKIT_PATH)
        foundation = ctypes.CDLL(FOUNDATION_PATH)
        matching = iokit.IOBSDNameMatching
        matching.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p]
        matching.restype = ctypes.c_void_p
        services = iokit.IOServiceGetMatchingServices
        services.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        services.restype = ctypes.c_int
        following = iokit.IOIteratorNext
        following.argtypes = [ctypes.c_uint32]
        following.restype = ctypes.c_uint32
        release = iokit.IOObjectRelease
        release.argtypes = [ctypes.c_uint32]
        release.restype = ctypes.c_int
        conforms = iokit.IOObjectConformsTo
        conforms.argtypes = [ctypes.c_uint32, ctypes.c_char_p]
        conforms.restype = ctypes.c_uint32
        entry = iokit.IORegistryEntryGetRegistryEntryID
        entry.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint64)]
        entry.restype = ctypes.c_int
        prop = iokit.IORegistryEntryCreateCFProperty
        prop.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        prop.restype = ctypes.c_void_p
        string = foundation.CFStringCreateWithCString
        string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        string.restype = ctypes.c_void_p
        type_of = foundation.CFGetTypeID
        type_of.argtypes = [ctypes.c_void_p]
        type_of.restype = ctypes.c_ulong
        boolean_type = foundation.CFBooleanGetTypeID
        boolean_type.argtypes = []
        boolean_type.restype = ctypes.c_ulong
        boolean_value = foundation.CFBooleanGetValue
        boolean_value.argtypes = [ctypes.c_void_p]
        boolean_value.restype = ctypes.c_uint8
        cf_release = foundation.CFRelease
        cf_release.argtypes = [ctypes.c_void_p]
        cf_release.restype = None
        self.matching: Callable[[int, int, bytes], int | None] = matching
        self.services: Callable[[int, int, object], int] = services
        self.following: Callable[[int], int] = following
        self.release: Callable[[int], int] = release
        self.conforms: Callable[[int, bytes], int] = conforms
        self.entry: Callable[[int, object], int] = entry
        self.prop: Callable[[int, int, int | None, int], int | None] = prop
        self.string: Callable[[int | None, bytes, int], int | None] = string
        self.type_of: Callable[[int], int] = type_of
        self.boolean_type: Callable[[], int] = boolean_type
        self.boolean_value: Callable[[int], int] = boolean_value
        self.cf_release: Callable[[int], None] = cf_release


@dataclass
class Accounting:
    acquired: int = 0
    released: int = 0
    entries: set[int] = field(default_factory=set[int])

    @property
    def balanced(self) -> bool:
        return self.acquired == self.released


class MediaSession:
    def __init__(self, bindings: Bindings, accounting: Accounting) -> None:
        self.bindings = bindings
        self.accounting = accounting
        self.service = 0
        self.entry_id = 0
        self.stable = True
        self.keys: dict[bytes, int] = {}

    def release_object(self, value: int) -> None:
        self.bindings.release(value)
        self.accounting.released += 1

    def release_value(self, value: int) -> None:
        self.bindings.cf_release(value)
        self.accounting.released += 1

    def key(self, name: bytes) -> int | None:
        existing = self.keys.get(name)
        if existing is not None:
            return existing
        created = self.bindings.string(None, name, UTF8_ENCODING)
        if not created:
            return None
        self.accounting.acquired += 1
        self.keys[name] = created
        return created

    def boolean(self, name: bytes) -> bool | None:
        key = self.key(name)
        if key is None or not self.service:
            return None
        value = self.bindings.prop(self.service, key, None, 0)
        if not value:
            return None
        self.accounting.acquired += 1
        try:
            if self.bindings.type_of(value) != self.bindings.boolean_type():
                return None
            return bool(self.bindings.boolean_value(value))
        finally:
            self.release_value(value)

    def current_entry(self) -> int | None:
        if not self.service:
            return None
        identifier = ctypes.c_uint64(0)
        if self.bindings.entry(self.service, ctypes.byref(identifier)) != 0:
            return None
        return identifier.value or None

    def lookup(self, device: str) -> bool:
        match = re.fullmatch(r"/dev/(disk[0-9]{1,8})", device)
        if match is None:
            return False
        return self.lookup_name(match[1])

    def lookup_name(self, name: str) -> bool:
        if re.fullmatch(r"disk[0-9]{1,8}(s[0-9]{1,4}){0,2}", name) is None:
            return False
        matching = self.bindings.matching(MAIN_PORT_DEFAULT, 0, name.encode())
        if not matching:
            return False
        iterator = ctypes.c_uint32(0)
        if (
            self.bindings.services(MAIN_PORT_DEFAULT, matching, ctypes.byref(iterator))
            != 0
            or not iterator.value
        ):
            return False
        self.accounting.acquired += 1
        found: list[int] = []
        try:
            while len(found) <= MATCH_LIMIT:
                item = self.bindings.following(iterator.value)
                if not item:
                    break
                self.accounting.acquired += 1
                found.append(item)
        finally:
            self.release_object(iterator.value)
        media = [item for item in found if self.bindings.conforms(item, b"IOMedia")]
        if len(found) != 1 or len(media) != 1:
            for item in found:
                self.release_object(item)
            return False
        self.service = media[0]
        whole = self.boolean(b"Whole")
        entry = self.current_entry()
        if whole is not True or entry is None:
            return False
        self.entry_id = entry
        self.accounting.entries.add(entry)
        return True

    def read(self) -> OpenRead:
        if not self.service or not self.entry_id:
            self.stable = False
            return "unavailable"
        if self.current_entry() != self.entry_id or self.boolean(b"Whole") is not True:
            self.stable = False
            return "unavailable"
        value = self.boolean(b"Open")
        if value is None:
            return "unavailable"
        return "true" if value else "false"

    def close(self) -> None:
        if self.service:
            self.release_object(self.service)
            self.service = 0
        for value in self.keys.values():
            self.release_value(value)
        self.keys = {}


class MediaReader:
    def __init__(self, bindings: Bindings | None = None) -> None:
        self.bindings = bindings if bindings is not None else Bindings()
        self.accounting = Accounting()

    def session(
        self, device: str, *, name: str | None = None
    ) -> tuple[MediaSession, bool]:
        session = MediaSession(self.bindings, self.accounting)
        try:
            found = (
                session.lookup(device) if name is None else session.lookup_name(name)
            )
        except BaseException:
            session.close()
            raise
        if not found:
            session.stable = False
        return session, found
