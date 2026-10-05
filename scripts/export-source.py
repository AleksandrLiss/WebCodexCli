#!/usr/bin/env python3
"""Export an allowlisted source archive without credentials, backups or git metadata."""
import argparse
import hashlib
import io
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent.parent
FILES = ['README.md', 'CHANGELOG.md', 'THIRD_PARTY_NOTICES.md', 'Dockerfile', 'compose.yaml',
         '.env.example', '.gitignore', '.dockerignore', '.gitattributes', 'requirements.txt',
         'requirements.lock', 'server.py', 'download-assets.py', 'run.sh']
DIRECTORIES = ['static', 'scripts', 'docs', 'tests', '.github']


def source_files():
    paths = [ROOT / name for name in FILES]
    for name in DIRECTORIES:
        paths.extend(sorted((ROOT / name).rglob('*')))
    for path in paths:
        if path.is_symlink():
            raise ValueError('Source export does not include symlinks: ' + str(path))
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if '__pycache__' in relative.parts or path.suffix in ('.pyc', '.pem', '.key', '.log'):
            continue
        if path.name == 'auth.json' or (path.name.startswith('.env') and relative.as_posix() != '.env.example'):
            continue
        yield path


def export(output):
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Export archive must be outside the project directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    secret = None
    if (ROOT / '.env').exists():
        for line in (ROOT / '.env').read_text().splitlines():
            if line.startswith('WEB_PASSWORD='):
                secret = line.partition('=')[2].encode()
    manifest = []
    paths = list(source_files())
    for path in paths:
        data = path.read_bytes()
        if secret and secret in data:
            raise ValueError('Live web password found in source file: ' + path.relative_to(ROOT).as_posix())
        if b'-----BEGIN PRIVATE KEY-----\n' in data or b'-----BEGIN RSA PRIVATE KEY-----\n' in data:
            raise ValueError('Private key found in source file: ' + str(path))
        manifest.append(hashlib.sha256(data).hexdigest() + '  ' + path.relative_to(ROOT).as_posix())
    with tarfile.open(output, 'w:gz', compresslevel=1) as archive:
        for path in paths:
            archive.add(path, arcname='codex-web/' + path.relative_to(ROOT).as_posix(), recursive=False)
        data = ('\n'.join(manifest) + '\n').encode()
        entry = tarfile.TarInfo('codex-web/SHA256SUMS')
        entry.size = len(data)
        entry.mode = 0o644
        archive.addfile(entry, io.BytesIO(data))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(digest + '  ' + output.name + '\n')
    print(f'{output}\nFiles: {len(paths)}\nSHA256: {digest}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    export(args.output)
