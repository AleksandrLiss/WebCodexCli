#!/usr/bin/env python3
"""Verify manual lifecycle on a disposable web container, without production volumes."""
import importlib.util
import json
from pathlib import Path
import secrets

spec = importlib.util.spec_from_file_location('manage', Path(__file__).parents[1] / 'scripts/manage.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main():
    m.preflight()
    name = 'codex-web-manual-test-' + secrets.token_hex(5)
    values = dict(m.DEFAULTS, CONTAINER_NAME=name)
    try:
        m.docker('run', '-d', '--name', name, '--label', m.LABEL + '=true',
                 '--restart', 'unless-stopped', '--network', 'none',
                 '--memory=96m', '--memory-swap=96m', '--cpus=0.5', '--pids-limit=64',
                 '--health-interval=2s', '--health-timeout=5s', '--health-start-period=3s',
                 '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                 '--env', 'WEB_PASSWORD=fixture-password-for-manual-check',
                 values['IMAGE_NAME'], capture=True)
        m.docker('stop', name, capture=True)
        m.start(values)
        item = m.inspect(name)
        assert item['State']['Running']
        assert item['HostConfig']['RestartPolicy']['Name'] == 'no'
        started = item['State']['StartedAt']
        m.start(values)
        assert m.inspect(name)['State']['StartedAt'] == started
        m.stop(values)
        item = m.inspect(name)
        assert not item['State']['Running']
        assert item['HostConfig']['RestartPolicy']['Name'] == 'no'
        m.stop(values)
        print('PASS: real manual start/stop, healthy startup, legacy policy migration, idempotency')
    except Exception:
        item = m.inspect(name)
        if item:
            print(json.dumps(item['State']))
            logs = m.docker('logs', '--tail', '10', name, capture=True, check=False)
            print(logs.stdout.decode(errors='replace'))
            print(logs.stderr.decode(errors='replace'))
        raise
    finally:
        m.docker('rm', '-f', name, capture=True, check=False)


if __name__ == '__main__':
    main()
