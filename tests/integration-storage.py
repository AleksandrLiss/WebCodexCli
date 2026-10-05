#!/usr/bin/env python3
"""Real backup/restore round trip on unique disposable volumes; no model calls."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import tempfile

spec = importlib.util.spec_from_file_location('manage', Path(__file__).parents[1] / 'scripts/manage.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main():
    m.preflight()
    prefix = 'codex-web-storage-test-' + secrets.token_hex(5)
    image = os.getenv('STORAGE_TEST_IMAGE', 'codex-web:local')
    m.docker('image', 'inspect', image, capture=True)
    values = dict(m.DEFAULTS, IMAGE_NAME=image, CONTAINER_NAME=prefix,
                  WEB_PASSWORD='fixture-password-no-real-credentials',
                  WORKSPACE_VOLUME=prefix + '-workspace', CODEX_HOME_VOLUME=prefix + '-home')
    restored = dict(values, WORKSPACE_VOLUME=prefix + '-restored-workspace', CODEX_HOME_VOLUME=prefix + '-restored-home')
    volumes = [values['WORKSPACE_VOLUME'], values['CODEX_HOME_VOLUME'], restored['WORKSPACE_VOLUME'], restored['CODEX_HOME_VOLUME']]
    try:
        # Docker initializes fresh volumes with uid 1000 directory ownership from the image.
        m.docker('run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                 '--memory=64m', '--memory-swap=64m', '--cpus=0.25',
                 '--mount', f'source={values["WORKSPACE_VOLUME"]},target=/workspace',
                 '--mount', f'source={values["CODEX_HOME_VOLUME"]},target=/home/codex/.codex',
                 image, 'python', '-c',
                 "from pathlib import Path; import json; "
                 "root=Path('/workspace/project'); root.mkdir(parents=True); "
                 "(root/'sample.txt').write_text('round-trip-data'); "
                 "auth=Path('/home/codex/.codex/auth.json'); "
                 "auth.write_text(json.dumps({'fixture':True})); auth.chmod(0o600)", capture=True)
        m.docker('run', '-d', '--name', prefix, '--label', m.LABEL + '=true',
                 '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                 '--memory=64m', '--memory-swap=64m', '--cpus=0.25',
                 '--mount', f'source={values["WORKSPACE_VOLUME"]},target=/workspace',
                 '--mount', f'source={values["CODEX_HOME_VOLUME"]},target=/home/codex/.codex',
                 image, 'sleep', '600', capture=True)
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            root = Path(directory)
            env = root / '.env'
            env.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
            env.chmod(0o600)
            m.backup(values, env, root / 'backup')
            assert m.inspect(prefix)['State']['Running'], 'Backup must restart its previous container'
            m.verify_backup(root / 'backup')
            m.docker('rm', '-f', prefix, capture=True)
            m.restore(restored, env, root / 'backup', False)
            data = m.helper(restored, 'cat', '/data/project/sample.txt', mount=f'source={restored["WORKSPACE_VOLUME"]},target=/data,readonly')
            assert data.stdout == b'round-trip-data'
            auth = m.helper(restored, 'cat', '/data/auth.json', mount=f'source={restored["CODEX_HOME_VOLUME"]},target=/data,readonly')
            assert json.loads(auth.stdout) == {'fixture': True}
            mode = m.helper(restored, 'stat', '-c', '%a', '/data/auth.json', mount=f'source={restored["CODEX_HOME_VOLUME"]},target=/data,readonly')
            assert mode.stdout.strip() == b'600', 'Credential cache permissions must survive migration'
        print('PASS: real backup/restore, service restart, volume ownership, file content, auth mode 600')
    finally:
        m.docker('rm', '-f', prefix, capture=True, check=False)
        for name in volumes:
            m.docker('volume', 'rm', name, capture=True, check=False)


if __name__ == '__main__':
    main()
