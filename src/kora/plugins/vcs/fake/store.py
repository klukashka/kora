import hashlib
import json
import os
import re
import signal
import stat
import struct
import sys
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from types import FrameType
from typing import Final, NamedTuple, cast

KIB: Final[int] = 1024
MIB: Final[int] = 1024 * KIB
MAX_REVISIONS: Final[int] = 1000
MAX_PREPARED_REVISIONS: Final[int] = MAX_REVISIONS + 1
MAX_MESSAGE_BYTES: Final[int] = 64 * KIB
MAX_REVISION_BYTES: Final[int] = 80 * KIB
MAX_TREE_ENTRIES: Final[int] = 50_000
MAX_TREE_BYTES: Final[int] = 64 * MIB
MAX_BLOB_BYTES: Final[int] = 256 * MIB
MAX_CHAIN_BYTES: Final[int] = 512 * MIB
MAX_CHAIN_BLOBS: Final[int] = 100_000
MAX_ANCESTRY: Final[int] = 10_000
MAX_ANCESTRY_TREE_BYTES: Final[int] = 256 * MIB
MAX_INDEX_RECORDS: Final[int] = 100_000
MAX_PATH_BYTES: Final[int] = 4096
MAX_COMPONENT_BYTES: Final[int] = 255
MAX_SYMLINK_BYTES: Final[int] = 4096
MAX_CONTROL_BYTES: Final[int] = 8192
IDENTIFIER: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
READ_FLAGS: Final[int] = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
DIRECTORY_FLAGS: Final[int] = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
NEW_FLAGS: Final[int] = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
)
RECORD_HEADER: Final[struct.Struct] = struct.Struct(">cQ")
REVISION_KIND: Final[bytes] = b"R"
TREE_KIND: Final[bytes] = b"T"
BLOB_KIND: Final[bytes] = b"B"
REGULAR: Final[str] = "regular"
EXECUTABLE: Final[str] = "executable"
SYMLINK: Final[str] = "symlink"
MODES: Final[frozenset[str]] = frozenset({REGULAR, EXECUTABLE, SYMLINK})
SEALED_MODES: Final[dict[str, str]] = {
    "100644": REGULAR,
    "100755": EXECUTABLE,
    "120000": SYMLINK,
}
ENTRY_KEYS: Final[frozenset[str]] = frozenset({"path", "mode", "size", "sha256"})
REVISION_KEYS: Final[frozenset[str]] = frozenset({"parent", "message", "tree"})
SECTIONS: Final[tuple[str, ...]] = ("revisions", "trees", "blobs")
RESERVED: Final[frozenset[str]] = frozenset({".git", ".kora", "git~1", "kora~1"})
IGNORABLE: Final[frozenset[str]] = frozenset("‌‍‎‏‪‫‬‭‮⁪⁫⁬⁭⁮⁯﻿")
EXCLUDED: Final[frozenset[str]] = frozenset({".git", ".kora"})
CANDIDATE_STORE: Final[tuple[str, ...]] = (".kora", "dirvcs")
PREPARED_STORE: Final[str] = "store"
HEAD_NAME: Final[str] = "head"
PREPARED_NAME: Final[str] = "prepared"
CHECKOUT_NAME: Final[str] = "checkout.json"
INDEX_NAME: Final[str] = "index.jsonl"
PREPARED_DIGEST_NAME: Final[str] = "prepared_digest"
COMMIT_MESSAGE: Final[re.Pattern[str]] = re.compile(r"kora: \S+ #[1-9][0-9]*")
REFUSED: Final[int] = 3
FAILED: Final[int] = 4


class Refused(Exception):
    pass


class Failed(Exception):
    pass


class Entry(NamedTuple):
    path: str
    mode: str
    size: int
    sha256: str


class Revision(NamedTuple):
    identifier: str
    parent: str | None
    message: str
    tree: str
    size: int


class Pin(NamedTuple):
    path: str
    device: int
    inode: int


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_identifier(value: object) -> bool:
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def checked_id(value: object) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise Refused
    return value


def canonical(document: object) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def refuse_constant(name: str) -> object:
    raise Refused


def canonical_document(data: bytes) -> object:
    try:
        document: object = json.loads(data, parse_constant=refuse_constant)
    except (ValueError, RecursionError):
        raise Refused from None
    if canonical(document) != data:
        raise Refused
    return document


def is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def reserved(component: str) -> bool:
    visible = "".join(item for item in component if item not in IGNORABLE)
    trimmed = visible.split(":", 1)[0].rstrip(". ")
    return unicodedata.normalize("NFC", trimmed).casefold() in RESERVED


def path_allowed(path: str) -> bool:
    if not path or path.startswith("/") or len(path.encode("utf-8")) > MAX_PATH_BYTES:
        return False
    if any(ord(item) < 0x20 or ord(item) == 0x7F for item in path):
        return False
    return all(
        part not in ("", ".", "..")
        and len(part.encode("utf-8")) <= MAX_COMPONENT_BYTES
        and not reserved(part)
        for part in path.split("/")
    )


def folded(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def check_tree(entries: Sequence[Entry]) -> None:
    if len(entries) > MAX_TREE_ENTRIES:
        raise Refused
    paths: set[str] = set()
    parents: set[str] = set()
    for entry in entries:
        if not path_allowed(entry.path):
            raise Refused
        key = folded(entry.path)
        if key in paths:
            raise Refused
        paths.add(key)
        parts = key.split("/")
        parents.update("/".join(parts[:index]) for index in range(1, len(parts)))
    if paths & parents:
        raise Refused


def entry_of(item: object) -> Entry:
    if not isinstance(item, dict):
        raise Refused
    fields = cast("dict[str, object]", item)
    if fields.keys() != ENTRY_KEYS:
        raise Refused
    path, mode, size = fields["path"], fields["mode"], fields["size"]
    if not isinstance(path, str) or mode not in MODES or not isinstance(mode, str):
        raise Refused
    if not is_count(size) or not isinstance(size, int) or size > MAX_BLOB_BYTES:
        raise Refused
    return Entry(path, mode, size, checked_id(fields["sha256"]))


def tree_of(data: bytes) -> tuple[Entry, ...]:
    if len(data) > MAX_TREE_BYTES:
        raise Refused
    document = canonical_document(data)
    if not isinstance(document, list):
        raise Refused
    entries = tuple(entry_of(item) for item in cast("list[object]", document))
    if [entry.path for entry in entries] != sorted({entry.path for entry in entries}):
        raise Refused
    check_tree(entries)
    return entries


def tree_document(entries: Sequence[Entry]) -> bytes:
    ordered = sorted(entries, key=lambda entry: entry.path)
    return canonical(
        [
            {
                "path": entry.path,
                "mode": entry.mode,
                "size": entry.size,
                "sha256": entry.sha256,
            }
            for entry in ordered
        ]
    )


def is_message(message: object) -> bool:
    return (
        isinstance(message, str) and len(message.encode("utf-8")) <= MAX_MESSAGE_BYTES
    )


def revision_of(data: bytes) -> Revision:
    if len(data) > MAX_REVISION_BYTES:
        raise Refused
    document = canonical_document(data)
    if not isinstance(document, dict):
        raise Refused
    fields = cast("dict[str, object]", document)
    if fields.keys() != REVISION_KEYS:
        raise Refused
    parent, message = fields["parent"], fields["message"]
    if not isinstance(message, str) or not is_message(message):
        raise Refused
    return Revision(
        identifier=digest_of(data),
        parent=None if parent is None else checked_id(parent),
        message=message,
        tree=checked_id(fields["tree"]),
        size=len(data),
    )


def revision_document(parent: str | None, message: str, tree: str) -> bytes:
    return canonical({"parent": parent, "message": message, "tree": tree})


def open_directory(parent: int | None, name: str) -> int:
    if parent is None:
        return os.open(name, DIRECTORY_FLAGS)
    return os.open(name, DIRECTORY_FLAGS, dir_fd=parent)


def make_directory(parent: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    return open_directory(parent, name)


def read_limited(directory: int, name: str, limit: int) -> bytes:
    descriptor = os.open(name, READ_FLAGS, dir_fd=directory)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_size > limit:
            raise Refused
        chunks: list[bytes] = []
        size = 0
        while block := os.read(descriptor, min(MIB, limit + 1 - size)):
            size += len(block)
            if size > limit:
                raise Refused
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(descriptor, view) :]


def same_content(directory: int, name: str, digest: str, limit: int) -> bool:
    try:
        return stream_digest(directory, name, limit) == digest
    except (OSError, Refused):
        return False


def stream_digest(directory: int, name: str, limit: int) -> str:
    descriptor = os.open(name, READ_FLAGS, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise Refused
        hasher = hashlib.sha256()
        size = 0
        while block := os.read(descriptor, MIB):
            size += len(block)
            if size > limit:
                raise Refused
            hasher.update(block)
        return hasher.hexdigest()
    finally:
        os.close(descriptor)


class Scratch:
    def __init__(self, directory: int, name: str, descriptor: int) -> None:
        self.directory = directory
        self.name = name
        self.descriptor = descriptor


SCRATCH: Final[set[Scratch]] = set()


def scratch_file(directory: int) -> Scratch:
    name = f".tmp-{os.urandom(8).hex()}"
    descriptor = os.open(name, NEW_FLAGS, 0o600, dir_fd=directory)
    scratch = Scratch(directory, name, descriptor)
    SCRATCH.add(scratch)
    return scratch


def discard(scratch: Scratch) -> None:
    if scratch.descriptor >= 0:
        os.close(scratch.descriptor)
        scratch.descriptor = -1
    try:
        os.unlink(scratch.name, dir_fd=scratch.directory)
    except FileNotFoundError:
        pass
    SCRATCH.discard(scratch)


def discard_all() -> None:
    for scratch in list(SCRATCH):
        discard(scratch)


def settle(
    scratch: Scratch, target: str, digest: str, limit: int, durable: bool
) -> None:
    if durable:
        os.fsync(scratch.descriptor)
    os.close(scratch.descriptor)
    scratch.descriptor = -1
    try:
        os.link(
            scratch.name,
            target,
            src_dir_fd=scratch.directory,
            dst_dir_fd=scratch.directory,
            follow_symlinks=False,
        )
    except FileExistsError:
        if not same_content(scratch.directory, target, digest, limit):
            raise Failed from None
    finally:
        discard(scratch)


def put_bytes(directory: int, name: str, data: bytes, durable: bool = False) -> None:
    scratch = scratch_file(directory)
    try:
        write_all(scratch.descriptor, data)
    except BaseException:
        discard(scratch)
        raise
    settle(scratch, name, digest_of(data), len(data), durable)


def copy_object(
    source: int, target: int, name: str, limit: int, durable: bool = False
) -> None:
    scratch = scratch_file(target)
    try:
        descriptor = os.open(name, READ_FLAGS, dir_fd=source)
        try:
            digest = copied_digest(descriptor, scratch.descriptor, limit)
        finally:
            os.close(descriptor)
    except BaseException:
        discard(scratch)
        raise
    if digest != name:
        discard(scratch)
        raise Refused
    settle(scratch, name, name, limit, durable)


def copied_digest(source: int, target: int, limit: int) -> str:
    if not stat.S_ISREG(os.fstat(source).st_mode):
        raise Refused
    hasher = hashlib.sha256()
    size = 0
    while block := os.read(source, MIB):
        size += len(block)
        if size > limit:
            raise Refused
        hasher.update(block)
        write_all(target, block)
    return hasher.hexdigest()


class Store:
    def __init__(self, root: int, create: bool = False) -> None:
        self.root = root
        opener = make_directory if create else open_directory
        self.sections: dict[str, int] = {}
        try:
            for section in SECTIONS:
                self.sections[section] = opener(root, section)
        except OSError:
            self.close()
            raise

    def close(self) -> None:
        for descriptor in self.sections.values():
            os.close(descriptor)
        self.sections.clear()

    @property
    def revisions(self) -> int:
        return self.sections["revisions"]

    @property
    def trees(self) -> int:
        return self.sections["trees"]

    @property
    def blobs(self) -> int:
        return self.sections["blobs"]

    def revision(self, identifier: str) -> Revision:
        revision = revision_of(
            read_limited(self.revisions, checked_id(identifier), MAX_REVISION_BYTES)
        )
        if revision.identifier != identifier:
            raise Refused
        return revision

    def tree_bytes(self, identifier: str) -> bytes:
        data = read_limited(self.trees, checked_id(identifier), MAX_TREE_BYTES)
        if digest_of(data) != identifier:
            raise Refused
        return data

    def tree(self, identifier: str) -> tuple[Entry, ...]:
        return tree_of(self.tree_bytes(identifier))

    def has(self, section: int, identifier: str) -> bool:
        try:
            status = os.stat(identifier, dir_fd=section, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return stat.S_ISREG(status.st_mode)


def ancestry(
    store: Store,
    base: str,
    limit: int = MAX_ANCESTRY,
    tree_bytes: int = MAX_ANCESTRY_TREE_BYTES,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[Revision]:
    found: list[Revision] = []
    total = 0
    current: str | None = base
    while current is not None:
        if cancelled():
            raise Failed
        revision = store.revision(current)
        found.append(revision)
        total += os.stat(
            revision.tree, dir_fd=store.trees, follow_symlinks=False
        ).st_size
        if len(found) > limit or total > tree_bytes:
            raise Refused
        current = revision.parent
    return found


def new_revisions(
    store: Store,
    head: str,
    base: str,
    limit: int,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[Revision]:
    found: list[Revision] = []
    current: str | None = head
    while current != base:
        if current is None or len(found) >= limit or cancelled():
            raise Refused
        revision = store.revision(current)
        found.append(revision)
        current = revision.parent
    found.reverse()
    return found


def base_sizes(
    store: Store,
    lineage: Sequence[Revision],
    wanted: set[str],
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, int]:
    held: dict[str, int] = {}
    seen: set[str] = set()
    for revision in lineage:
        if cancelled():
            raise Failed
        if revision.tree in seen:
            continue
        seen.add(revision.tree)
        for entry in store.tree(revision.tree):
            if entry.sha256 in wanted:
                held.setdefault(entry.sha256, entry.size)
    return held


def changes(
    before: Sequence[Entry], after: Sequence[Entry]
) -> tuple[list[Entry], set[str]]:
    previous = {entry.path: entry for entry in before}
    introduced = [
        entry
        for entry in after
        if (old := previous.get(entry.path)) is None
        or (old.mode, old.sha256) != (entry.mode, entry.sha256)
    ]
    current = {entry.path for entry in after}
    paths = {entry.path for entry in introduced} | (previous.keys() - current)
    return introduced, paths


def parent_tree(store: Store, revision: Revision) -> tuple[Entry, ...]:
    if revision.parent is None:
        return ()
    return store.tree(store.revision(revision.parent).tree)


def history(
    store: Store, base: str, prepared: str
) -> Iterator[tuple[Revision, list[Entry], set[str]]]:
    before = store.tree(store.revision(base).tree)
    for revision in new_revisions(store, prepared, base, MAX_PREPARED_REVISIONS):
        after = store.tree(revision.tree)
        introduced, paths = changes(before, after)
        yield revision, introduced, paths
        before = after


def new_blobs(store: Store, base: str, prepared: str) -> set[str]:
    named: set[str] = set()
    for revision in new_revisions(store, prepared, base, MAX_PREPARED_REVISIONS):
        named.update(entry.sha256 for entry in store.tree(revision.tree))
    held = base_sizes(store, ancestry(store, base), named)
    return named - held.keys()


def read_pin(path: str) -> Pin:
    descriptor = os.open(path, READ_FLAGS)
    try:
        data = os.read(descriptor, MAX_CONTROL_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_CONTROL_BYTES:
        raise Failed
    document: object = json.loads(data)
    if not isinstance(document, dict):
        raise Failed
    fields = cast("dict[str, object]", document)
    path_value, device, inode = (
        fields.get("path"),
        fields.get("device"),
        fields.get("inode"),
    )
    if not isinstance(path_value, str) or not is_count(device) or not is_count(inode):
        raise Failed
    return Pin(path_value, cast("int", device), cast("int", inode))


def open_repository(control: str) -> int:
    pin = read_pin(control)
    descriptor = os.open(pin.path, DIRECTORY_FLAGS)
    status = os.fstat(descriptor)
    if (status.st_dev, status.st_ino) != (pin.device, pin.inode):
        os.close(descriptor)
        raise Failed
    return descriptor


def is_branch(name: str) -> bool:
    if not name or len(name.encode("utf-8")) > MAX_COMPONENT_BYTES:
        return False
    return all(
        part
        and part not in (".", "..")
        and not part.startswith((".", "-"))
        and not any(ord(item) < 0x20 or ord(item) == 0x7F for item in part)
        for part in name.split("/")
    )


def emit(text: str) -> None:
    sys.stdout.write(text)


def read_text_file(directory: int, name: str) -> str:
    return read_limited(directory, name, MAX_CONTROL_BYTES).decode("ascii").strip()


class Opened:
    def __init__(self) -> None:
        self.descriptors: list[int] = []
        self.stores: list[Store] = []

    def directory(self, parent: int | None, name: str, create: bool = False) -> int:
        if create and parent is not None:
            descriptor = make_directory(parent, name)
        else:
            descriptor = open_directory(parent, name)
        self.descriptors.append(descriptor)
        return descriptor

    def repository(self, control: str) -> Store:
        root = open_repository(control)
        self.descriptors.append(root)
        return self.store(root)

    def store(self, root: int, create: bool = False) -> Store:
        opened = Store(root, create)
        self.stores.append(opened)
        return opened

    def close(self) -> None:
        for store in self.stores:
            store.close()
        for descriptor in self.descriptors:
            os.close(descriptor)


def check_repository(control: str) -> None:
    opened = Opened()
    try:
        opened.repository(control)
        opened.directory(opened.descriptors[0], "heads")
    finally:
        opened.close()


def verify(control: str, revisions: Sequence[str]) -> None:
    opened = Opened()
    try:
        repository = opened.repository(control)
        for identifier in revisions:
            repository.tree(repository.revision(identifier).tree)
    finally:
        opened.close()


def descend(root: int, parts: Sequence[str], opened: Opened) -> int:
    current = root
    for part in parts:
        current = opened.directory(current, part, create=True)
    return current


def made_parent(root: int, parts: Sequence[str]) -> int:
    current = os.dup(root)
    try:
        for part in parts:
            following = make_directory(current, part)
            os.close(current)
            current = following
    except BaseException:
        os.close(current)
        raise
    return current


def materialise_entry(blobs: int, root: int, entry: Entry) -> None:
    parts = entry.path.split("/")
    parent = made_parent(root, parts[:-1])
    try:
        if entry.mode != SYMLINK:
            materialise_file(blobs, entry, parent, parts[-1])
            return
        target = read_limited(blobs, entry.sha256, MAX_SYMLINK_BYTES)
        if digest_of(target) != entry.sha256:
            raise Failed
        os.symlink(target, parts[-1], dir_fd=parent)
    finally:
        os.close(parent)


def materialise(control: str, revision: str, branch: str) -> None:
    opened = Opened()
    try:
        repository = opened.repository(control)
        checkout = repository.revision(revision)
        tree_data = repository.tree_bytes(checkout.tree)
        entries = tree_of(tree_data)
        root = opened.directory(None, ".")
        for entry in sorted(entries, key=lambda item: item.mode == SYMLINK):
            materialise_entry(repository.blobs, root, entry)
        store_root = descend(root, CANDIDATE_STORE, opened)
        candidate = opened.store(store_root, create=True)
        put_bytes(candidate.trees, checkout.tree, tree_data)
        put_bytes(
            store_root,
            CHECKOUT_NAME,
            canonical({"revision": revision, "tree": checkout.tree, "branch": branch}),
        )
        put_bytes(store_root, HEAD_NAME, revision.encode("ascii"))
    finally:
        opened.close()


def materialise_file(blobs: int, entry: Entry, parent: int, name: str) -> None:
    source = os.open(entry.sha256, READ_FLAGS, dir_fd=blobs)
    try:
        mode = 0o755 if entry.mode == EXECUTABLE else 0o644
        target = os.open(name, NEW_FLAGS, mode, dir_fd=parent)
        try:
            hasher = hashlib.sha256()
            while block := os.read(source, MIB):
                hasher.update(block)
                write_all(target, block)
            os.fchmod(target, mode)
        finally:
            os.close(target)
    finally:
        os.close(source)
    if hasher.hexdigest() != entry.sha256:
        raise Failed


def changed(control: str, base: str, revision: str) -> None:
    opened = Opened()
    try:
        repository = opened.repository(control)
        before = repository.tree(repository.revision(base).tree)
        after = repository.tree(repository.revision(revision).tree)
        _, paths = changes(before, after)
    finally:
        opened.close()
    emit(json.dumps(sorted(paths), ensure_ascii=False))


class WorkingFile(NamedTuple):
    path: str
    mode: str
    data: bytes | None
    parent: int
    name: str


def working_files(root: int, prefix: str = "") -> Iterator[WorkingFile]:
    for name in sorted(os.listdir(root)):
        if not prefix and name in EXCLUDED:
            continue
        path = f"{prefix}{name}"
        status = os.stat(name, dir_fd=root, follow_symlinks=False)
        if stat.S_ISDIR(status.st_mode):
            directory = open_directory(root, name)
            try:
                yield from working_files(directory, f"{path}/")
            finally:
                os.close(directory)
        elif stat.S_ISLNK(status.st_mode):
            target = os.readlink(name, dir_fd=root).encode("utf-8", "surrogateescape")
            yield WorkingFile(path, SYMLINK, target, root, name)
        else:
            mode = EXECUTABLE if status.st_mode & stat.S_IXUSR else REGULAR
            yield WorkingFile(path, mode, None, root, name)


def stored_blob(blobs: int, item: WorkingFile, known: set[str]) -> Entry:
    if item.data is not None:
        sha = digest_of(item.data)
        if sha not in known:
            put_bytes(blobs, sha, item.data)
        return Entry(item.path, item.mode, len(item.data), sha)
    descriptor = os.open(item.name, READ_FLAGS, dir_fd=item.parent)
    scratch = scratch_file(blobs)
    try:
        hasher = hashlib.sha256()
        size = 0
        while block := os.read(descriptor, MIB):
            size += len(block)
            hasher.update(block)
            write_all(scratch.descriptor, block)
    except BaseException:
        discard(scratch)
        raise
    finally:
        os.close(descriptor)
    sha = hasher.hexdigest()
    if sha in known:
        discard(scratch)
    else:
        settle(scratch, sha, sha, MAX_BLOB_BYTES, False)
    return Entry(item.path, item.mode, size, sha)


def commit(message: str) -> None:
    if not is_message(message):
        raise Refused
    opened = Opened()
    try:
        root = opened.directory(None, ".")
        store_root = descend(root, CANDIDATE_STORE, opened)
        candidate = opened.store(store_root)
        head = read_text_file(store_root, HEAD_NAME)
        checkout = checkout_record(store_root)
        known = {entry.sha256 for entry in candidate.tree(checkout[1])}
        entries = [
            stored_blob(candidate.blobs, item, known) for item in working_files(root)
        ]
        tree_data = tree_document(entries)
        tree = digest_of(tree_data)
        put_bytes(candidate.trees, tree, tree_data)
        document = revision_document(head, message, tree)
        identifier = digest_of(document)
        put_bytes(candidate.revisions, identifier, document)
        os.unlink(HEAD_NAME, dir_fd=store_root)
        put_bytes(store_root, HEAD_NAME, identifier.encode("ascii"))
    finally:
        opened.close()
    emit(identifier)


def checkout_record(store_root: int) -> tuple[str, str]:
    document: object = json.loads(
        read_limited(store_root, CHECKOUT_NAME, MAX_CONTROL_BYTES)
    )
    if not isinstance(document, dict):
        raise Refused
    fields = cast("dict[str, object]", document)
    return checked_id(fields.get("revision")), checked_id(fields.get("tree"))


def record(descriptor: int, kind: bytes, data: bytes) -> None:
    write_all(descriptor, RECORD_HEADER.pack(kind, len(data)))
    write_all(descriptor, data)


def blob_record(descriptor: int, blobs: int, sha: str) -> None:
    source = os.open(sha, READ_FLAGS, dir_fd=blobs)
    try:
        size = os.fstat(source).st_size
        write_all(descriptor, RECORD_HEADER.pack(BLOB_KIND, size))
        remaining = size
        while remaining:
            block = os.read(source, min(MIB, remaining))
            if not block:
                raise Refused
            remaining -= len(block)
            write_all(descriptor, block)
    finally:
        os.close(source)


def fresh_output(root: int, path: str, opened: Opened) -> int:
    parts = path.split("/")
    parent = descend(root, parts[:-1], opened)
    try:
        status = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return opened.directory(parent, parts[-1], create=True)
    if not stat.S_ISDIR(status.st_mode):
        os.unlink(parts[-1], dir_fd=parent)
    else:
        output = opened.directory(parent, parts[-1])
        for name in os.listdir(output):
            os.unlink(name, dir_fd=output)
        return output
    return opened.directory(parent, parts[-1], create=True)


def export(checkout: str, out: str) -> None:
    opened = Opened()
    try:
        root = opened.directory(None, ".")
        store_root = descend(root, CANDIDATE_STORE, opened)
        candidate = opened.store(store_root)
        head = checked_id(read_text_file(store_root, HEAD_NAME))
        recorded, checkout_tree = checkout_record(store_root)
        if recorded != checkout:
            raise Refused
        output = fresh_output(root, out, opened)
        bundle = head != checkout
        if bundle:
            write_chain(candidate, head, checkout, checkout_tree, output)
        manifest = "".join(
            json.dumps({"path": item.path, "gitlink": None}, ensure_ascii=False) + "\n"
            for item in working_files(root)
        )
        put_bytes(output, "manifest.jsonl", manifest.encode("utf-8"))
        put_bytes(
            output,
            "head.json",
            json.dumps({"head": head, "bundle": bundle}).encode("ascii"),
        )
    finally:
        opened.close()


def write_chain(
    candidate: Store, head: str, checkout: str, checkout_tree: str, output: int
) -> None:
    revisions = new_revisions(candidate, head, checkout, MAX_REVISIONS)
    known = {entry.sha256 for entry in candidate.tree(checkout_tree)}
    trees = [revision.tree for revision in revisions if revision.tree != checkout_tree]
    blobs: list[str] = []
    for tree in dict.fromkeys(trees):
        for entry in candidate.tree(tree):
            if entry.sha256 not in known:
                known.add(entry.sha256)
                blobs.append(entry.sha256)
    descriptor = os.open("head.bundle", NEW_FLAGS, 0o600, dir_fd=output)
    try:
        for revision in revisions:
            record(
                descriptor,
                REVISION_KIND,
                read_limited(
                    candidate.revisions, revision.identifier, MAX_REVISION_BYTES
                ),
            )
        for tree in dict.fromkeys(trees):
            record(descriptor, TREE_KIND, candidate.tree_bytes(tree))
        for sha in blobs:
            blob_record(descriptor, candidate.blobs, sha)
    finally:
        os.close(descriptor)


def check_ancestry(control: str, base: str) -> None:
    opened = Opened()
    try:
        ancestry(opened.repository(control), checked_id(base))
    finally:
        opened.close()


def copy_base(control: str, base: str) -> None:
    opened = Opened()
    try:
        repository = opened.repository(control)
        root = opened.directory(None, ".")
        prepared = opened.store(
            opened.directory(root, PREPARED_STORE, create=True), create=True
        )
        for revision in ancestry(repository, checked_id(base)):
            copy_object(
                repository.revisions,
                prepared.revisions,
                revision.identifier,
                MAX_REVISION_BYTES,
            )
            if not prepared.has(prepared.trees, revision.tree):
                copy_object(
                    repository.trees, prepared.trees, revision.tree, MAX_TREE_BYTES
                )
    finally:
        opened.close()


def read_head_document(sealed: int) -> tuple[str, bool]:
    document: object = json.loads(read_limited(sealed, "head.json", MAX_CONTROL_BYTES))
    if not isinstance(document, dict):
        raise Refused
    fields = cast("dict[str, object]", document)
    bundle = fields.get("bundle")
    if not isinstance(bundle, bool):
        raise Refused
    return checked_id(fields.get("head")), bundle


class Chain:
    def __init__(self) -> None:
        self.revisions: list[Revision] = []
        self.revision_bytes: dict[str, bytes] = {}
        self.trees: dict[str, bytes] = {}
        self.entries: dict[str, tuple[Entry, ...]] = {}
        self.blobs: dict[str, int] = {}


class ChainReader:
    def __init__(self, descriptor: int, size: int, staging: int) -> None:
        self.descriptor = descriptor
        self.remaining = size
        self.staging = staging
        self.staged: dict[str, Scratch] = {}

    def exact(self, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            block = os.read(self.descriptor, min(MIB, remaining))
            if not block:
                raise Refused
            remaining -= len(block)
            chunks.append(block)
        self.remaining -= size
        return b"".join(chunks)

    def header(self) -> tuple[bytes, int] | None:
        if self.remaining == 0:
            return None
        if self.remaining < RECORD_HEADER.size:
            raise Refused
        kind, size = cast(
            "tuple[bytes, int]", RECORD_HEADER.unpack(self.exact(RECORD_HEADER.size))
        )
        bound = {
            REVISION_KIND: MAX_REVISION_BYTES,
            TREE_KIND: MAX_TREE_BYTES,
            BLOB_KIND: MAX_BLOB_BYTES,
        }.get(kind)
        if bound is None or size > bound or size > self.remaining:
            raise Refused
        return kind, size

    def blob(self, size: int) -> tuple[str, int]:
        scratch = scratch_file(self.staging)
        hasher = hashlib.sha256()
        remaining = size
        while remaining:
            block = os.read(self.descriptor, min(MIB, remaining))
            if not block:
                raise Refused
            remaining -= len(block)
            hasher.update(block)
            write_all(scratch.descriptor, block)
        os.close(scratch.descriptor)
        scratch.descriptor = -1
        self.remaining -= size
        sha = hasher.hexdigest()
        if sha in self.staged:
            raise Refused
        self.staged[sha] = scratch
        return sha, size


def read_chain(reader: ChainReader, base: str) -> Chain:
    chain = Chain()
    section = REVISION_KIND
    order = (REVISION_KIND, TREE_KIND, BLOB_KIND)
    while (header := reader.header()) is not None:
        kind, size = header
        if order.index(kind) < order.index(section):
            raise Refused
        section = kind
        if kind == REVISION_KIND:
            data = reader.exact(size)
            revision = revision_of(data)
            expected = chain.revisions[-1].identifier if chain.revisions else base
            if revision.parent != expected or len(chain.revisions) >= MAX_REVISIONS:
                raise Refused
            chain.revisions.append(revision)
            chain.revision_bytes[revision.identifier] = data
        elif kind == TREE_KIND:
            data = reader.exact(size)
            tree = digest_of(data)
            if tree in chain.trees:
                raise Refused
            chain.entries[tree] = tree_of(data)
            chain.trees[tree] = data
        else:
            sha, blob_size = reader.blob(size)
            chain.blobs[sha] = blob_size
            if len(chain.blobs) > MAX_CHAIN_BLOBS:
                raise Refused
    return chain


def check_chain(chain: Chain, head: str, prepared: Store, base: str) -> set[str]:
    if not chain.revisions or chain.revisions[-1].identifier != head:
        raise Refused
    named_trees = {revision.tree for revision in chain.revisions}
    if not chain.trees.keys() <= named_trees:
        raise Refused
    for tree in named_trees - chain.trees.keys():
        if not prepared.has(prepared.trees, tree):
            raise Refused
    sizes: dict[str, int] = {}
    for entries in chain.entries.values():
        for entry in entries:
            if sizes.setdefault(entry.sha256, entry.size) != entry.size:
                raise Refused
    if not chain.blobs.keys() <= sizes.keys():
        raise Refused
    held = base_sizes(prepared, ancestry(prepared, base), set(sizes))
    for sha, size in sizes.items():
        known = held.get(sha, chain.blobs.get(sha))
        if known != size:
            raise Refused
    return set(held)


def import_chain(input_location: str, base: str) -> None:
    opened = Opened()
    staging_name = f"incoming-{os.urandom(8).hex()}"
    root = opened.directory(None, ".")
    store_root = opened.directory(root, PREPARED_STORE)
    prepared = opened.store(store_root)
    staging = opened.directory(store_root, staging_name, create=True)
    try:
        sealed = opened.directory(None, input_location)
        head, bundle = read_head_document(sealed)
        if not bundle:
            if head != base:
                raise Refused
            put_bytes(store_root, HEAD_NAME, head.encode("ascii"))
            return
        if head == base:
            raise Refused
        descriptor = os.open("head.bundle", READ_FLAGS, dir_fd=sealed)
        opened.descriptors.append(descriptor)
        size = os.fstat(descriptor).st_size
        if size > MAX_CHAIN_BYTES:
            raise Refused
        reader = ChainReader(descriptor, size, staging)
        chain = read_chain(reader, base)
        held = check_chain(chain, head, prepared, base)
        for tree, data in chain.trees.items():
            put_bytes(prepared.trees, tree, data)
        for sha, scratch in reader.staged.items():
            if sha in held:
                continue
            settle_staged(scratch, prepared.blobs, sha)
        for revision in chain.revisions:
            put_bytes(
                prepared.revisions,
                revision.identifier,
                chain.revision_bytes[revision.identifier],
            )
        put_bytes(store_root, HEAD_NAME, head.encode("ascii"))
    finally:
        discard_all()
        os.rmdir(staging_name, dir_fd=store_root)
        opened.close()


def settle_staged(scratch: Scratch, blobs: int, sha: str) -> None:
    try:
        os.link(
            scratch.name,
            sha,
            src_dir_fd=scratch.directory,
            dst_dir_fd=blobs,
            follow_symlinks=False,
        )
    except FileExistsError:
        if not same_content(blobs, sha, sha, MAX_BLOB_BYTES):
            raise Refused from None
    finally:
        discard(scratch)


class SealedRecord(NamedTuple):
    entry: Entry
    chunk: int
    offset: int


def sealed_record(line: bytes) -> SealedRecord:
    document: object = json.loads(line)
    if not isinstance(document, dict):
        raise Refused
    fields = cast("dict[str, object]", document)
    path, mode = fields.get("path"), SEALED_MODES.get(str(fields.get("mode")))
    size, chunk, offset = fields.get("size"), fields.get("chunk"), fields.get("offset")
    if not isinstance(path, str) or mode is None or fields.get("object") is not None:
        raise Refused
    if not (is_count(size) and is_count(chunk) and is_count(offset)):
        raise Refused
    digest = fields.get("sha256")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise Refused
    entry = Entry(path, mode, cast("int", size), checked_id(digest[len("sha256:") :]))
    if entry.size > MAX_BLOB_BYTES:
        raise Refused
    return SealedRecord(entry, cast("int", chunk), cast("int", offset))


def sealed_records(sealed: int) -> list[SealedRecord]:
    descriptor = os.open("tree.jsonl", READ_FLAGS, dir_fd=sealed)
    records: list[SealedRecord] = []
    with os.fdopen(descriptor, "rb") as stream:
        for line in stream:
            records.append(sealed_record(line))
            if len(records) > MAX_TREE_ENTRIES:
                raise Refused
    check_tree([item.entry for item in records])
    return records


def import_sealed(
    sealed: int, item: SealedRecord, blobs: int, present: set[str]
) -> None:
    entry = item.entry
    chunk = os.open(f"tree.{item.chunk}.bin", READ_FLAGS, dir_fd=sealed)
    scratch = None if entry.sha256 in present else scratch_file(blobs)
    try:
        os.lseek(chunk, item.offset, os.SEEK_SET)
        hasher = hashlib.sha256()
        remaining = entry.size
        while remaining:
            block = os.read(chunk, min(MIB, remaining))
            if not block:
                raise Refused
            remaining -= len(block)
            hasher.update(block)
            if scratch is not None:
                write_all(scratch.descriptor, block)
    except BaseException:
        if scratch is not None:
            discard(scratch)
        raise
    finally:
        os.close(chunk)
    if hasher.hexdigest() != entry.sha256:
        if scratch is not None:
            discard(scratch)
        raise Refused
    if scratch is not None:
        settle(scratch, entry.sha256, entry.sha256, MAX_BLOB_BYTES, False)
        present.add(entry.sha256)


def build(input_location: str, base: str, message: str) -> None:
    if COMMIT_MESSAGE.fullmatch(message) is None:
        raise Refused
    opened = Opened()
    try:
        root = opened.directory(None, ".")
        store_root = opened.directory(root, PREPARED_STORE)
        prepared = opened.store(store_root)
        sealed = opened.directory(None, input_location)
        head = checked_id(read_text_file(store_root, HEAD_NAME))
        head_tree = prepared.revision(head).tree
        records = sealed_records(sealed)
        wanted = {item.entry.sha256 for item in records}
        present = set(base_sizes(prepared, ancestry(prepared, base), wanted))
        present.update(sha for sha in wanted if prepared.has(prepared.blobs, sha))
        for item in records:
            import_sealed(sealed, item, prepared.blobs, present)
        tree_data = tree_document([item.entry for item in records])
        if len(tree_data) > MAX_TREE_BYTES:
            raise Refused
        tree = digest_of(tree_data)
        result = head
        if tree != head_tree:
            put_bytes(prepared.trees, tree, tree_data)
            document = revision_document(head, message, tree)
            result = digest_of(document)
            put_bytes(prepared.revisions, result, document)
        if result == base:
            raise Refused
        put_bytes(store_root, PREPARED_NAME, result.encode("ascii"))
    finally:
        discard_all()
        opened.close()


def prepared_head() -> None:
    opened = Opened()
    try:
        store_root = opened.directory(opened.directory(None, "."), PREPARED_STORE)
        emit(checked_id(read_text_file(store_root, PREPARED_NAME)))
    finally:
        opened.close()


def opened_prepared(opened: Opened) -> Store:
    return opened.store(opened.directory(opened.directory(None, "."), PREPARED_STORE))


def index_line(
    revision: str, path: str | None, kind: str, location: str, size: int
) -> str:
    return json.dumps(
        {
            "revision": revision,
            "path": path,
            "kind": kind,
            "content_location": location,
            "content_digest": "sha256:" + location,
            "size_bytes": size,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def index_lines(store: Store, base: str, prepared: str) -> list[str]:
    fresh = new_blobs(store, base, prepared)
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for revision, introduced, _ in history(store, base, prepared):
        lines.append(
            index_line(
                revision.identifier,
                None,
                "revision_metadata",
                revision.identifier,
                revision.size,
            )
        )
        for entry in introduced:
            key = (entry.sha256, entry.path)
            if entry.sha256 not in fresh or key in seen:
                continue
            seen.add(key)
            kind = "symlink" if entry.mode == SYMLINK else "file"
            lines.append(
                index_line(
                    revision.identifier, entry.path, kind, entry.sha256, entry.size
                )
            )
        if len(lines) > MAX_INDEX_RECORDS:
            raise Refused
    return lines


def decode(base: str, prepared: str) -> None:
    opened = Opened()
    try:
        root = opened.directory(None, ".")
        lines = index_lines(opened_prepared(opened), base, prepared)
        data = "".join(line + "\n" for line in lines).encode("utf-8")
        put_bytes(root, INDEX_NAME, data)
        prepared_digest = read_text_file(root, PREPARED_DIGEST_NAME)
    finally:
        discard_all()
        opened.close()
    manifest = {
        "prepared_digest": prepared_digest,
        "revision": prepared,
        "content_index_location": INDEX_NAME,
        "content_index_digest": "sha256:" + digest_of(data),
        "complete": True,
    }
    emit(json.dumps(manifest, sort_keys=True))


def paths(base: str, prepared: str) -> None:
    opened = Opened()
    found: set[str] = set()
    try:
        for _, _, changed_paths in history(opened_prepared(opened), base, prepared):
            found.update(changed_paths)
    finally:
        opened.close()
    emit(json.dumps(sorted(found), ensure_ascii=False))


def diff_stat(base: str, prepared: str) -> None:
    opened = Opened()
    try:
        store = opened_prepared(opened)
        before = store.tree(store.revision(base).tree)
        after = store.tree(store.revision(prepared).tree)
        _, changed_paths = changes(before, after)
    finally:
        opened.close()
    count = len(changed_paths)
    emit(f" {count} file{'' if count == 1 else 's'} changed")


def publish_objects(prepared: Store, repository: Store, base: str, head: str) -> None:
    fresh = new_blobs(prepared, base, head)
    revisions = new_revisions(prepared, head, base, MAX_PREPARED_REVISIONS)
    for sha in sorted(fresh):
        copy_object(prepared.blobs, repository.blobs, sha, MAX_BLOB_BYTES, True)
    for tree in dict.fromkeys(revision.tree for revision in revisions):
        copy_object(prepared.trees, repository.trees, tree, MAX_TREE_BYTES, True)
    for revision in revisions:
        copy_object(
            prepared.revisions,
            repository.revisions,
            revision.identifier,
            MAX_REVISION_BYTES,
            True,
        )


def publish(control: str, branch: str, base: str, prepared: str) -> None:
    if not is_branch(branch):
        raise Refused
    opened = Opened()
    try:
        repository = opened.repository(control)
        store = opened_prepared(opened)
        publish_objects(store, repository, base, prepared)
        parts = branch.split("/")
        heads = opened.directory(repository.root, "heads")
        parent = descend(heads, parts[:-1], opened)
        scratch = scratch_file(parent)
        write_all(scratch.descriptor, prepared.encode("ascii"))
        os.fsync(scratch.descriptor)
        os.close(scratch.descriptor)
        scratch.descriptor = -1
        try:
            os.link(
                scratch.name,
                parts[-1],
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
        except FileExistsError:
            raise Failed from None
        finally:
            discard(scratch)
    finally:
        discard_all()
        opened.close()


COMMANDS: Final[dict[str, tuple[str, ...]]] = {
    "repository": ("repository",),
    "verify": ("repository", "revision"),
    "materialise": ("repository", "revision", "branch"),
    "show": ("repository", "revision"),
    "branch": ("branch",),
    "changed": ("repository", "base", "revision"),
    "commit": ("message",),
    "export": ("checkout", "out"),
    "ancestry": ("repository", "base"),
    "base": ("repository", "base"),
    "import": ("input", "base"),
    "build": ("input", "base", "message"),
    "prepared": (),
    "decode": ("base", "prepared"),
    "paths": ("base", "prepared"),
    "stat": ("base", "prepared"),
    "publish": ("repository", "branch", "base", "prepared"),
}


def parsed_arguments(arguments: Sequence[str]) -> dict[str, object]:
    if not arguments or arguments[0] not in COMMANDS:
        raise Refused
    command, rest = arguments[0], arguments[1:]
    allowed = COMMANDS[command]
    values: dict[str, object] = {"command": command}
    if len(rest) % 2:
        raise Refused
    for option, value in zip(rest[::2], rest[1::2], strict=True):
        name = option.removeprefix("--")
        if option == name or name not in allowed:
            raise Refused
        if name == "revision":
            listed = values.setdefault("revision", [])
            cast("list[str]", listed).append(value)
        elif name in values:
            raise Refused
        else:
            values[name] = value
    if set(allowed) - values.keys():
        raise Refused
    return values


def revisions_of(values: dict[str, object]) -> list[str]:
    listed = values.get("revision")
    if not isinstance(listed, list):
        raise Refused
    return [checked_id(item) for item in cast("list[object]", listed)]


def text(values: dict[str, object], name: str) -> str:
    value = values[name]
    if not isinstance(value, str):
        raise Refused
    return value


def run(values: dict[str, object]) -> None:
    command = text(values, "command")
    if command == "repository":
        check_repository(text(values, "repository"))
    elif command == "verify":
        verify(text(values, "repository"), revisions_of(values))
    elif command == "materialise":
        branch = text(values, "branch")
        if not is_branch(branch):
            raise Refused
        materialise(text(values, "repository"), revisions_of(values)[0], branch)
    elif command == "show":
        identifier = revisions_of(values)[0]
        verify(text(values, "repository"), [identifier])
        emit(identifier)
    elif command == "branch":
        branch = text(values, "branch")
        if not is_branch(branch):
            raise Refused
        emit(branch)
    elif command == "changed":
        base, revision = checked_id(text(values, "base")), revisions_of(values)[0]
        changed(text(values, "repository"), base, revision)
    elif command == "commit":
        commit(text(values, "message"))
    elif command == "export":
        export(checked_id(text(values, "checkout")), text(values, "out"))
    else:
        run_publisher(command, values)


def run_publisher(command: str, values: dict[str, object]) -> None:
    if command == "ancestry":
        check_ancestry(text(values, "repository"), text(values, "base"))
    elif command == "base":
        copy_base(text(values, "repository"), text(values, "base"))
    elif command == "import":
        import_chain(text(values, "input"), checked_id(text(values, "base")))
    elif command == "build":
        build(
            text(values, "input"),
            checked_id(text(values, "base")),
            text(values, "message"),
        )
    elif command == "prepared":
        prepared_head()
    else:
        base = checked_id(text(values, "base"))
        prepared = checked_id(text(values, "prepared"))
        if command == "decode":
            decode(base, prepared)
        elif command == "paths":
            paths(base, prepared)
        elif command == "stat":
            diff_stat(base, prepared)
        else:
            publish(text(values, "repository"), text(values, "branch"), base, prepared)


def terminate(number: int, frame: FrameType | None) -> None:
    sys.exit(128 + number)


def main(arguments: list[str]) -> int:
    signal.signal(signal.SIGTERM, terminate)
    try:
        run(parsed_arguments(arguments))
    except (Refused, ValueError, OSError):
        return REFUSED
    except Failed:
        return FAILED
    finally:
        discard_all()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
