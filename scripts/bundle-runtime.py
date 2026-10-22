"""Copy the builder's Python 3.12 runtime and relocate its Mach-O dependencies.

Build on the same architecture as the destination Mac. No user data is packaged.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

if sys.version_info[:2] != (3, 12):
    raise SystemExit('Packaging currently requires a Python 3.12 virtual environment.')
base = Path(sys.base_prefix)
target = Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)

def ignore(directory, names):
    return [n for n in names if n in ('__pycache__', 'site-packages', 'test', 'tests', 'idlelib', 'ensurepip') or n.endswith(('.pyc', '.a'))]

shutil.copytree(base / 'lib', target / 'lib', symlinks=True, ignore=ignore)
(target / 'bin').mkdir()
# Use the actual interpreter; bin/python3.12 is a framework launcher stub.
shutil.copy2(base / 'Resources/Python.app/Contents/MacOS/Python', target / 'bin/python3.12')
shutil.copy2(base / 'Python', target / 'Python')
packages = Path(sys.prefix) / 'lib/python3.12/site-packages'
shutil.copytree(packages, target / 'lib/python3.12/site-packages', symlinks=True,
                ignore=lambda d, names: [n for n in names if n == '__pycache__' or n.endswith('.pyc')])
# CPython's standard library includes LICENSE.txt; preserve package license metadata.
for path in target.rglob('*'):
    if path.is_symlink():
        destination = os.readlink(path)
        if destination.startswith(str(base)):
            replacement = target / Path(destination).relative_to(base)
            path.unlink(); path.symlink_to(os.path.relpath(replacement, path.parent))

native = [target/'bin/python3.12', target/'Python'] + [p for p in target.rglob('*') if p.suffix in ('.so','.dylib') and not p.is_symlink()]
for path in native:
    libraries = subprocess.check_output(['/usr/bin/otool', '-L', str(path)], text=True)
    identity_output = subprocess.check_output(['/usr/bin/otool', '-D', str(path)], text=True)
    identities = {line.strip() for line in identity_output.splitlines()[1:] if line.strip() and not line.rstrip().endswith(':')}
    changes = ['-id', '@rpath/' + path.name] if identities else []
    for line in libraries.splitlines()[1:]:
        if not line.startswith('\t'): continue
        dependency = line.strip().split(' (')[0]
        if dependency in identities: continue
        if dependency.startswith(str(base) + '/'):
            relocated = target / Path(dependency).relative_to(base)
            if not relocated.exists(): raise RuntimeError(f'Missing bundled library: {dependency}')
            changes += ['-change', dependency, '@loader_path/' + os.path.relpath(relocated, path.parent)]
        elif dependency.startswith('/') and not dependency.startswith(('/System/', '/usr/lib/')):
            raise RuntimeError(f'Unbundled native dependency: {path}: {dependency}')
    if changes:
        subprocess.run(['/usr/bin/install_name_tool', *changes, str(path)], check=True, capture_output=True)
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(path)], check=True, capture_output=True)
print(f'Bundled Python and dependencies: {target}')
