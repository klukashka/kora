import json
import os
import sys
from pathlib import Path

from diag.media_open_reader import MediaReader
from tests.plugins.storage import pressure_images
from tests.plugins.storage.pressure_controller import create


def absent_disk(reader: MediaReader) -> dict[str, str]:
    session, found = reader.session("/dev/disk99999")
    try:
        value = session.read()
    finally:
        session.close()
    return {"found": "yes" if found else "no", "read": value}


def slice_name(reader: MediaReader) -> dict[str, str]:
    session, found = reader.session("/dev/disk0s1")
    try:
        value = session.read()
    finally:
        session.close()
    return {"found": "yes" if found else "no", "read": value}


def slice_iokit(reader: MediaReader) -> dict[str, str]:
    session, found = reader.session("", name="disk0s1")
    try:
        value = session.read()
    finally:
        session.close()
    return {"found": "yes" if found else "no", "read": value}


def detached_media(reader: MediaReader, directory: Path) -> dict[str, str]:
    with create(directory) as controller:
        controller.acquire("enospc")
        stamp = controller.manifest.image_stamp
        if stamp is None:
            raise SystemExit("image stamp missing")
        attachment = pressure_images.discover(directory, stamp)
        if attachment is None:
            raise SystemExit("attachment missing")
        session, found = reader.session(attachment.device)
        try:
            mounted = session.read()
            controller.cleanup()
            after = session.read()
        finally:
            session.close()
        closed = controller.manifest.resources_closed
    return {
        "found": "yes" if found else "no",
        "read_mounted": mounted,
        "read_after_detach": after,
        "resources_closed": "yes" if closed else "no",
    }


def main() -> int:
    os.umask(0o077)
    if len(sys.argv) != 3:
        raise SystemExit("usage: media_open_reader_check.py SCRATCH OUTPUT")
    scratch = Path(sys.argv[1])
    output = Path(sys.argv[2])
    reader = MediaReader()
    result: dict[str, dict[str, str] | str] = {
        "absent_disk": absent_disk(reader),
        "slice_name": slice_name(reader),
        "slice_iokit": slice_iokit(reader),
        "detached_media": detached_media(reader, scratch / "reader-check"),
    }
    result["handles_balanced"] = "yes" if reader.accounting.balanced else "no"
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
