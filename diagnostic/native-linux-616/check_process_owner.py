from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        address = str(Path(directory) / 'ready.sock')
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(address)
        listener.listen(1)
        listener.settimeout(5)
        worker = 'import os,signal,socket,sys; s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); s.sendall(str(os.getpid()).encode()); signal.pause()'
        owner = 'import json,sys; from process_owner import command,UNSETTLED_CHILDREN,UNCONFIRMED_GROUPS\ntry:\n command([sys.executable,"-c",sys.argv[1],sys.argv[2]])\nexcept KeyboardInterrupt:\n assert UNSETTLED_CHILDREN and all(p.poll() is not None for p in UNSETTLED_CHILDREN)\n assert not UNCONFIRMED_GROUPS\n print(json.dumps({"interrupted_child_reaped":True}))\nelse:\n raise AssertionError("interrupt missing")'
        process = subprocess.Popen([sys.executable, '-c', owner, worker, address], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            connection, _ = listener.accept()
            with connection:
                child_pid = int(connection.recv(64))
            os.kill(process.pid, signal.SIGINT)
            output, error = process.communicate(timeout=10)
            if process.returncode != 0 or error or json.loads(output) != {'interrupted_child_reaped': True}:
                raise RuntimeError('interrupt_case_failed')
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError('child_survived')
            sys.stdout.write(output.decode())
        finally:
            listener.close()
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


if __name__ == '__main__':
    main()
