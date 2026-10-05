#!/usr/bin/env python3
"""Deployment and migration tools. Python standard library + Docker only."""
import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parent.parent
LABEL = 'io.codex-web.managed'
DEFAULTS = {
    'WEB_PASSWORD': '', 'WEB_BIND_ADDRESS': '0.0.0.0', 'WEB_PORT': '8080',
    'CONTAINER_NAME': 'codex-web', 'IMAGE_NAME': 'codex-web:local',
    'WORKSPACE_VOLUME': 'codex-web-workspace', 'CODEX_HOME_VOLUME': 'codex-web-home',
    'MEMORY_LIMIT': '384m', 'CPU_LIMIT': '0.75', 'PIDS_LIMIT': '128',
    'BUILD_MEMORY_LIMIT': '256m', 'BUILD_CPU_QUOTA': '50000', 'CODEX_VERSION': '0.160.0',
}


def fail(message):
    raise RuntimeError(message)


def config(path, password_required=True):
    values = DEFAULTS.copy()
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, separator, value = line.partition('=')
            if not separator or key not in DEFAULTS:
                fail(f'Unknown configuration key: {key}')
            if value.startswith(('"', "'")):
                fail(f'Use unquoted literal values: {key}')
            values[key] = value
    if password_required and len(values['WEB_PASSWORD']) < 16:
        fail('WEB_PASSWORD must contain at least 16 characters; run init-env')
    ipaddress.ip_address(values['WEB_BIND_ADDRESS'])
    if not values['WEB_PORT'].isdigit() or not 1 <= int(values['WEB_PORT']) <= 65535:
        fail('WEB_PORT must be 1..65535')
    for key in ('CONTAINER_NAME', 'WORKSPACE_VOLUME', 'CODEX_HOME_VOLUME'):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', values[key]):
            fail(f'Invalid Docker name: {key}')
    if values['WORKSPACE_VOLUME'] == values['CODEX_HOME_VOLUME']:
        fail('Workspace and Codex home must use different volumes')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./:@-]*', values['IMAGE_NAME']):
        fail('Invalid IMAGE_NAME')
    for key in ('MEMORY_LIMIT', 'BUILD_MEMORY_LIMIT'):
        if not re.fullmatch(r'[1-9][0-9]*[kKmMgG]', values[key]):
            fail(f'Invalid memory size: {key}')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', values['CPU_LIMIT']) or float(values['CPU_LIMIT']) <= 0:
        fail('CPU_LIMIT must be positive')
    for key in ('PIDS_LIMIT', 'BUILD_CPU_QUOTA'):
        if not values[key].isdigit() or int(values[key]) <= 0:
            fail(f'{key} must be a positive integer')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', values['CODEX_VERSION']):
        fail('CODEX_VERSION must be a pinned numeric version')
    return values


def initialize(path):
    values = config(path, password_required=False)
    if not values['WEB_PASSWORD']:
        values['WEB_PASSWORD'] = secrets.token_urlsafe(24)
    if len(values['WEB_PASSWORD']) < 16:
        fail('Existing WEB_PASSWORD is too short; edit it explicitly')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(''.join(f'{key}={value}\n' for key, value in values.items()))
    temp.replace(path)
    print(f'Configuration ready: {path} (mode 600). Password is not printed.')
    return values


def docker(*args, capture=False, check=True, **kwargs):
    output = kwargs.pop('stdout', None)
    return subprocess.run(['docker', *map(str, args)], check=check,
                          stdout=subprocess.PIPE if capture else output,
                          stderr=subprocess.PIPE if capture else None, **kwargs)


def preflight():
    if not shutil.which('docker'):
        fail('Docker not installed; run scripts/install-docker.sh')
    if docker('info', capture=True, check=False).returncode:
        fail('Docker daemon unavailable. Start Docker or run with sudo.')
    if os.uname().machine not in ('x86_64', 'aarch64'):
        fail('Supported architectures: x86_64 and aarch64')


def inspect(name):
    result = docker('container', 'inspect', name, capture=True, check=False)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def managed(container, values):
    labels = container['Config'].get('Labels') or {}
    if labels.get(LABEL) != 'true' and container['Config']['Image'] != values['IMAGE_NAME']:
        fail('Refusing to modify an unrelated container with the same name')


def manual_policy(values, container):
    if container['HostConfig']['RestartPolicy']['Name'] != 'no':
        docker('update', '--restart=no', values['CONTAINER_NAME'])


def start(values):
    preflight()
    item = inspect(values['CONTAINER_NAME'])
    if not item:
        fail('Container not found; run scripts/deploy.sh once to install it')
    managed(item, values)
    manual_policy(values, item)
    if not item['State']['Running']:
        docker('start', values['CONTAINER_NAME'])
    wait_healthy(values['CONTAINER_NAME'])
    print('Service is healthy. Automatic restart is disabled.')


def stop(values):
    preflight()
    item = inspect(values['CONTAINER_NAME'])
    if not item:
        fail('Container not found')
    managed(item, values)
    manual_policy(values, item)
    if item['State']['Running']:
        docker('stop', '--time', '15', values['CONTAINER_NAME'])
    print('Service is stopped. Automatic restart is disabled.')


def build_command(values):
    return ['build', '--memory=' + values['BUILD_MEMORY_LIMIT'],
            '--memory-swap=' + values['BUILD_MEMORY_LIMIT'], '--cpu-period=100000',
            '--cpu-quota=' + values['BUILD_CPU_QUOTA'], '--build-arg',
            'CODEX_VERSION=' + values['CODEX_VERSION'], '-t', values['IMAGE_NAME'], str(ROOT)]


def build(values):
    # These build cgroup flags are effective with the legacy builder, not BuildKit.
    docker(*build_command(values), env=dict(os.environ, DOCKER_BUILDKIT='0'))


def run_command(values, env_file):
    address = values['WEB_BIND_ADDRESS']
    if ':' in address:
        address = '[' + address + ']'
    return ['run', '-d', '--name', values['CONTAINER_NAME'], '--label', LABEL + '=true',
            '--init', '--restart', 'no', '-p', f'{address}:{values["WEB_PORT"]}:8080',
            '--env-file', str(env_file), '--mount', f'source={values["WORKSPACE_VOLUME"]},target=/workspace',
            '--mount', f'source={values["CODEX_HOME_VOLUME"]},target=/home/codex/.codex',
            '--security-opt', 'no-new-privileges:true', '--cap-drop', 'ALL',
            '--pids-limit', values['PIDS_LIMIT'], '--memory', values['MEMORY_LIMIT'],
            '--memory-swap', values['MEMORY_LIMIT'], '--cpus', values['CPU_LIMIT'],
            '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3', values['IMAGE_NAME']]


def wait_healthy(name, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = inspect(name)
        if not item or not item['State']['Running']:
            fail('Container stopped before becoming healthy; inspect docker logs')
        if item['State'].get('Health', {}).get('Status') == 'healthy':
            return
        time.sleep(1)
    fail('Health check did not pass within 60 seconds')


def deploy(values, env_file, skip_build=False, replace=False, dry_run=False):
    if dry_run:
        if not env_file.exists():
            print(f'Would generate private configuration: {env_file}')
        if not skip_build:
            print('DOCKER_BUILDKIT=0 ' + shlex.join(['docker', *build_command(values)]))
        print(shlex.join(['docker', *run_command(values, env_file)]))
        print('Would wait for health; replacement keeps the previous container for rollback.')
        return
    preflight()
    old = inspect(values['CONTAINER_NAME'])
    if old:
        managed(old, values)
        manual_policy(values, old)
        if not replace:
            if not old['State']['Running']:
                docker('start', values['CONTAINER_NAME'])
            wait_healthy(values['CONTAINER_NAME'])
            print('Existing deployment is healthy. Use --replace to apply changes.')
            return
    if not skip_build:
        build(values)
    docker('image', 'inspect', values['IMAGE_NAME'], capture=True)
    for key in ('WORKSPACE_VOLUME', 'CODEX_HOME_VOLUME'):
        docker('volume', 'create', values[key], capture=True)
    previous = None
    old_running = bool(old and old['State']['Running'])
    if old:
        previous = values['CONTAINER_NAME'] + '-previous-' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S') + '-' + secrets.token_hex(2)
        try:
            if old_running:
                docker('stop', '--time', '15', values['CONTAINER_NAME'])
            docker('rename', values['CONTAINER_NAME'], previous)
        except Exception:
            if old_running:
                docker('start', values['CONTAINER_NAME'], check=False)
            raise
    try:
        docker(*run_command(values, env_file))
        wait_healthy(values['CONTAINER_NAME'])
    except Exception:
        failed = inspect(values['CONTAINER_NAME'])
        if failed and (failed['Config'].get('Labels') or {}).get(LABEL) == 'true':
            docker('rm', '-f', values['CONTAINER_NAME'], check=False)
        if previous:
            docker('rename', previous, values['CONTAINER_NAME'])
            if old_running:
                docker('start', values['CONTAINER_NAME'])
            print('Previous container restored.', file=sys.stderr)
        raise
    print(f'Healthy: http://{values["WEB_BIND_ADDRESS"]}:{values["WEB_PORT"]} (use server IP for 0.0.0.0)')
    if previous:
        print(f'Previous container retained: {previous}')


def helper(values, *args, mount=None, stdin=None, stdout=None):
    command = ['run', '--rm', '--user', '1000:1000', '--network', 'none', '--read-only',
               '--security-opt', 'no-new-privileges:true', '--cap-drop', 'ALL',
               '--memory=64m', '--memory-swap=64m', '--cpus=0.25', '--pids-limit=32']
    if stdin is not None:
        command.append('-i')
    if mount:
        command += ['--mount', mount]
    return docker(*command, values['IMAGE_NAME'], *args, stdin=stdin, stdout=stdout, capture=stdout is None)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def backup(values, env_file, destination):
    preflight()
    container = inspect(values['CONTAINER_NAME'])
    if not container:
        fail('Container not found; deploy it before backing up')
    managed(container, values)
    mounts = {m['Destination']: m.get('Name') for m in container['Mounts']}
    if mounts.get('/workspace') != values['WORKSPACE_VOLUME'] or mounts.get('/home/codex/.codex') != values['CODEX_HOME_VOLUME']:
        fail('Configuration volumes differ from actual deployment; correct .env first')
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(destination, 0o700)
    was_running = container['State']['Running']
    try:
        if was_running:
            docker('stop', '--time', '15', values['CONTAINER_NAME'])
        for key, filename in [('WORKSPACE_VOLUME', 'workspace.tar.gz'), ('CODEX_HOME_VOLUME', 'codex-home.tar.gz')]:
            path = destination / filename
            with path.open('wb') as output:
                os.chmod(path, 0o600)
                helper(values, 'tar', '-czf', '-', '-C', '/data', '.',
                       mount=f'source={values[key]},target=/data,readonly', stdout=output)
        shutil.copyfile(env_file, destination / '.env')
        os.chmod(destination / '.env', 0o600)
        manifest = {'format': 1, 'created': datetime.now(timezone.utc).isoformat(),
                    'files': {name: sha256(destination / name) for name in ['workspace.tar.gz', 'codex-home.tar.gz', '.env']}}
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        os.chmod(destination / 'manifest.json', 0o600)
    finally:
        if was_running:
            docker('start', values['CONTAINER_NAME'])
    print(f'Backup ready: {destination}. Contains passwords and Codex credentials; keep private.')


def verify_backup(destination):
    manifest = json.loads((destination / 'manifest.json').read_text())
    names = {'workspace.tar.gz', 'codex-home.tar.gz', '.env'}
    if manifest.get('format') != 1 or set(manifest.get('files', {})) != names:
        fail('Unsupported backup manifest')
    for name in names:
        if sha256(destination / name) != manifest['files'][name]:
            fail('Backup checksum mismatch: ' + name)
    for name in ('workspace.tar.gz', 'codex-home.tar.gz'):
        with tarfile.open(destination / name, 'r|gz') as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    fail('Unsafe backup member: ' + member.name)
                if member.issym() or member.islnk():
                    parent = str(path.parent) if member.issym() else '.'
                    target = posixpath.normpath(posixpath.join(parent, member.linkname))
                    if member.linkname.startswith('/') or target == '..' or target.startswith('../'):
                        fail('Backup link points outside volume: ' + member.name)


def restore(values, env_file, source, use_backup_env):
    verify_backup(source)
    if use_backup_env:
        if env_file.exists():
            fail('--use-backup-env requires an absent destination .env; move it aside first')
        values = config(source / '.env')
    preflight()
    if inspect(values['CONTAINER_NAME']):
        fail('Restore requires no target container; use a fresh server or new names')
    docker('image', 'inspect', values['IMAGE_NAME'], capture=True)
    for key in ('WORKSPACE_VOLUME', 'CODEX_HOME_VOLUME'):
        exists = docker('volume', 'inspect', values[key], capture=True, check=False).returncode == 0
        if exists:
            result = helper(values, 'find', '/data', '-mindepth', '1', '-print', '-quit',
                            mount=f'source={values[key]},target=/data,readonly')
            if result.stdout.strip():
                fail('Refusing to overwrite nonempty volume: ' + values[key])
    for key in ('WORKSPACE_VOLUME', 'CODEX_HOME_VOLUME'):
        docker('volume', 'create', values[key], capture=True)
    # Initialize volume ownership from the image directories, which belong to uid 1000.
    docker('run', '--rm', '--user', '1000:1000', '--network', 'none', '--read-only',
           '--memory=64m', '--memory-swap=64m', '--cpus=0.25', '--cap-drop', 'ALL',
           '--mount', f'source={values["WORKSPACE_VOLUME"]},target=/workspace',
           '--mount', f'source={values["CODEX_HOME_VOLUME"]},target=/home/codex/.codex', values['IMAGE_NAME'], 'true', capture=True)
    for key, filename in [('WORKSPACE_VOLUME', 'workspace.tar.gz'), ('CODEX_HOME_VOLUME', 'codex-home.tar.gz')]:
        with (source / filename).open('rb') as data:
            helper(values, 'tar', '--no-same-owner', '-xzf', '-', '-C', '/data',
                   mount=f'source={values[key]},target=/data', stdin=data)
    if use_backup_env:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write((source / '.env').read_bytes())
    print('Data restored. Run deploy --skip-build to start the application.')


def status(values):
    preflight()
    item = inspect(values['CONTAINER_NAME'])
    if not item:
        fail('Container not found')
    print(json.dumps({'name': values['CONTAINER_NAME'], 'running': item['State']['Running'],
                      'health': item['State'].get('Health', {}).get('Status'),
                      'restart': item['HostConfig']['RestartPolicy']['Name'],
                      'memoryBytes': item['HostConfig']['Memory'], 'ports': item['HostConfig']['PortBindings']}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init-env')
    sub.add_parser('build')
    sub.add_parser('status')
    sub.add_parser('start')
    sub.add_parser('stop')
    deploy_parser = sub.add_parser('deploy')
    deploy_parser.add_argument('--skip-build', action='store_true')
    deploy_parser.add_argument('--replace', action='store_true')
    deploy_parser.add_argument('--dry-run', action='store_true')
    backup_parser = sub.add_parser('backup')
    backup_parser.add_argument('destination', type=Path)
    restore_parser = sub.add_parser('restore')
    restore_parser.add_argument('source', type=Path)
    restore_parser.add_argument('--use-backup-env', action='store_true')
    auth_parser = sub.add_parser('import-auth')
    auth_parser.add_argument('file', type=Path)
    args = parser.parse_args()
    args.env_file = args.env_file.resolve()
    if args.command == 'init-env':
        initialize(args.env_file)
        return
    dry_run = args.command == 'deploy' and args.dry_run
    if args.command in ('build', 'deploy') and not dry_run and not args.env_file.exists():
        initialize(args.env_file)
    required = args.command not in ('status', 'start', 'stop') and not dry_run
    if args.command == 'restore':
        required = not args.use_backup_env
    values = config(args.env_file, password_required=required)
    if args.command == 'build':
        preflight(); build(values)
    elif args.command == 'deploy':
        deploy(values, args.env_file, args.skip_build, args.replace, args.dry_run)
    elif args.command == 'status':
        status(values)
    elif args.command == 'start':
        start(values)
    elif args.command == 'stop':
        stop(values)
    elif args.command == 'backup':
        backup(values, args.env_file, args.destination.resolve())
    elif args.command == 'restore':
        if not args.use_backup_env and not args.env_file.exists():
            fail('Run init-env first or restore with --use-backup-env')
        restore(values, args.env_file, args.source.resolve(), args.use_backup_env)
    elif args.command == 'import-auth':
        preflight()
        item = inspect(values['CONTAINER_NAME'])
        if not item or not item['State']['Running']:
            fail('Target container must be running')
        managed(item, values)
        if not isinstance(json.loads(args.file.read_text()), dict):
            fail('Authentication cache must be a JSON object')
        with args.file.open('rb') as source:
            docker('exec', '-i', values['CONTAINER_NAME'], 'sh', '-c', 'umask 077; cat > "$CODEX_HOME/auth.json"', stdin=source)
        docker('exec', values['CONTAINER_NAME'], 'codex', 'login', 'status')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
