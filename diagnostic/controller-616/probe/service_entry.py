from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

from run_actual_linux import main


def enter() -> None:
    root = Path(sys.argv[3])
    deadline = float(os.environ["KORA616_STARTED"]) + 120.0
    if os.geteuid() != 0 or root.stat().st_uid != 0 or root.stat().st_mode & 0o077:
        raise ValueError("private_root_required")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as gate:
        gate.settimeout(min(20.0, max(0.0, deadline - time.monotonic())))
        gate.connect(str(root / "gate"))
        gate.sendall(b"R")
        if gate.recv(1) != b"G" or time.monotonic() >= deadline:
            raise RuntimeError("activation_refused")
        sys.argv = sys.argv[:3]
        complete = False
        try:
            main()
            complete = True
        finally:
            gate.sendall(b"0" if complete else b"1")


if __name__ == "__main__":
    enter()
