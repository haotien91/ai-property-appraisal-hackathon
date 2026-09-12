"""Build a pure-Python Lambda zip; dependencies must be installed in --deps."""
import argparse
import hashlib
import zipfile
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--deps', required=True)
p.add_argument('--output', default='/tmp/ntpc-artifact-api.zip')
a = p.parse_args()
root = Path(__file__).resolve().parents[2]
with zipfile.ZipFile(a.output, 'w', zipfile.ZIP_DEFLATED) as z:
    for name in ('app.py', 'splitter.py', 'directory.py'):
        z.write(root / 'services/artifact-import' / name, name)
    for package in ('simplejson', 'pypdf'):
        for path in sorted((Path(a.deps) / package).rglob('*')):
            if path.is_file() and path.suffix not in ('.so', '.pyc') and '__pycache__' not in path.parts:
                z.write(path, str(path.relative_to(a.deps)))
print(hashlib.sha256(Path(a.output).read_bytes()).hexdigest())
