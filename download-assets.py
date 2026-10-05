"""Fetch pinned official npm distributions; extract only the files we need."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request


def package(name, version):
    with urllib.request.urlopen(f'https://registry.npmjs.org/{name}/{version}') as response:
        meta = json.load(response)
    # Stream large CLI archives to disk; keep memory bounded on small servers.
    file = tempfile.TemporaryFile()
    digest = hashlib.sha1()
    with urllib.request.urlopen(meta['dist']['tarball']) as response:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            file.write(chunk)
    if digest.hexdigest() != meta['dist']['shasum']:
        file.close()
        raise ValueError('Package checksum mismatch')
    file.seek(0)
    return tarfile.open(fileobj=file, mode='r|gz')


def extract(archive, source, dest, mode=0o644):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with archive.extractfile(source) as source_file, dest.open('wb') as output:
        shutil.copyfileobj(source_file, output, length=1024 * 1024)
    dest.chmod(mode)


arch = os.uname().machine
suffix = {'x86_64': 'x64', 'aarch64': 'arm64'}[arch]
version = os.environ.get('CODEX_VERSION', '0.160.0')
with package('@openai/codex', f'{version}-linux-{suffix}') as archive:
    target = f'{arch}-unknown-linux-musl'
    prefix = f'package/vendor/{target}/'
    for member in archive:
        if member.isfile() and member.name.startswith(prefix):
            relative = Path(member.name[len(prefix):])
            if '..' in relative.parts or relative.is_absolute():
                raise ValueError('Invalid archive path')
            extract(archive, member, Path('/opt/codex') / relative, member.mode & 0o777)
with package('@xterm/xterm', '6.0.0') as archive:
    files = {'package/lib/xterm.js': 'xterm.js', 'package/css/xterm.css': 'xterm.css', 'package/LICENSE': 'xterm-LICENSE'}
    for member in archive:
        if member.name in files:
            extract(archive, member, Path('/app/static/vendor') / files[member.name])
with package('@xterm/addon-fit', '0.11.0') as archive:
    files = {'package/lib/addon-fit.js': 'addon-fit.js', 'package/LICENSE': 'addon-fit-LICENSE'}
    for member in archive:
        if member.name in files:
            extract(archive, member, Path('/app/static/vendor') / files[member.name])
