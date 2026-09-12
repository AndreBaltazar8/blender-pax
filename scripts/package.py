"""Build the installable extension with an explicit, platform-independent file list."""
from pathlib import Path
import zipfile
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addon/blender_pax'
DEST = ROOT / 'dist/blender_pax-0.1.0.zip'
DEST.parent.mkdir(exist_ok=True)
files = ['__init__.py', 'blender_manifest.toml', 'runtime/package.json',
         'runtime/package-lock.json', 'runtime/bridge.mjs', 'runtime/decode.mjs']
with zipfile.ZipFile(DEST, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for name in files:
        archive.write(SOURCE / name, name)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md'):
        archive.write(ROOT / name, name)
print(DEST)
print(f'{DEST.stat().st_size:,} bytes')
