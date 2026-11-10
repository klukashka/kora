import hashlib
import sys
from pathlib import Path

CYCLES = 850
CONTROL_STEP = 50
CONTROLS = 16


def arms() -> tuple[str, ...]:
    controls = {CONTROL_STEP * k for k in range(1, CONTROLS + 1)}
    result: list[str] = []
    rank = 0
    for index in range(CYCLES):
        if index in controls:
            result.append("control")
            continue
        result.append("O" if rank % 2 == 0 else "C")
        rank += 1
    return tuple(result)


def encoded() -> bytes:
    lines = ["index,arm"] + [f"{index},{arm}" for index, arm in enumerate(arms())]
    return ("\n".join(lines) + "\n").encode("ascii")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read(path: Path) -> tuple[str, ...]:
    content = path.read_bytes()
    if content != encoded():
        raise ValueError("schedule file differs from the fixed rule")
    return arms()


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: media_open_schedule.py OUTPUT")
    Path(sys.argv[1]).write_bytes(encoded())
    sys.stdout.write(digest(encoded()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
