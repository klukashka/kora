from __future__ import annotations

import json
import os
import sys


def main() -> None:
    mode = sys.argv[1]
    if mode == "stall":
        os.read(0, 1)
        raise RuntimeError("unexpected_activation")
    prepared = {
        "phase": "prepared",
        "mode": "blocked",
        "activation_required": True,
        "child_pid": os.getpid() if mode != "foreign" else os.getppid(),
    }
    os.write(1, json.dumps(prepared).encode() + b"\n")
    if os.read(0, 1) != b"G":
        raise ValueError("activation")
    if mode == "overflow":
        remaining = bytes(70000)
        while remaining:
            remaining = remaining[os.write(1, remaining) :]
    else:
        os.write(
            1,
            json.dumps(
                {
                    "phase": "reaped",
                    "mode": "blocked",
                    "exit_code": 1,
                    "ready": False,
                    "normal_unwind": False,
                    "parent_kill": False,
                }
            ).encode()
            + b"\n",
        )


if __name__ == "__main__":
    main()
