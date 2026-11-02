import hashlib
import json
from pathlib import Path

names = ('quota_linux', 'write_quota.py', 'sync_child.py', 'native_ids')
manifest = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in names}
Path('manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
