import json
import os
import subprocess
import sys

if os.geteuid() == 0:
    raise RuntimeError('requires_nonroot')

cases = ((['./quota_linux', 'get', '/dev/loop0', str(os.getuid()), '0'], 2), ([sys.executable, 'run_linux.py'], 1), ([sys.executable, 'run_blocked_sync.py'], 1))
for argv, expected in cases:
    result = subprocess.run(argv, capture_output=True, timeout=5, check=False)
    if result.returncode != expected:
        raise RuntimeError('unexpected_refusal_status')
    if argv[0] == sys.executable and b'requires_linux_sudo' not in result.stderr:
        raise RuntimeError('missing_privilege_refusal')
sys.stdout.write(json.dumps({'nonroot_refusals': 'passed'}) + '\n')
