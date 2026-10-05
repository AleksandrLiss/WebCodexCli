import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'scripts' / file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


m = module('manage', 'manage.py')
exporter = module('exporter', 'export-source.py')


def container(running=True, managed=True, restart='no'):
    return {'Config': {'Image': 'codex-web:local', 'Labels': {m.LABEL: 'true'} if managed else {}},
            'State': {'Running': running, 'Health': {'Status': 'healthy'}},
            'HostConfig': {'RestartPolicy': {'Name': restart}},
            'Mounts': [{'Destination': '/workspace', 'Name': 'codex-web-workspace'},
                       {'Destination': '/home/codex/.codex', 'Name': 'codex-web-home'}]}


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = self.root / '.env'
        self.values = dict(m.DEFAULTS, WEB_PASSWORD='test-password-for-deployment-only')
        self.env.write_text(''.join(f'{k}={v}\n' for k, v in self.values.items()))

    def test_wrappers_run_with_posix_sh_from_another_directory(self):
        fake_python = self.root / 'python3'
        fake_python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        fake_python.chmod(0o755)
        environment = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ.get('PATH', ''))
        project = Path(__file__).parents[1]
        scripts = {
            'run.sh': ['deploy'],
            'scripts/start.sh': ['start'],
            'scripts/stop.sh': ['stop'],
            'scripts/status.sh': ['status'],
            'scripts/deploy.sh': ['deploy'],
            'scripts/update.sh': ['deploy', '--replace'],
            'scripts/backup.sh': ['backup'],
            'scripts/restore.sh': ['restore'],
        }
        for name, arguments in scripts.items():
            with self.subTest(script=name):
                result = subprocess.run(['sh', str(project / name), 'argument with spaces'], cwd=self.root,
                                        env=environment, check=True, capture_output=True, text=True)
                forwarded = result.stdout.splitlines()
                self.assertEqual(Path(forwarded[0]).resolve(), (project / 'scripts/manage.py').resolve())
                self.assertEqual(forwarded[1:], arguments + ['argument with spaces'])

    def test_init_preserves_password_and_private_mode(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            first = m.initialize(self.env)
            second = m.initialize(self.env)
        self.assertEqual(first['WEB_PASSWORD'], second['WEB_PASSWORD'])
        self.assertNotIn(first['WEB_PASSWORD'], output.getvalue())
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)

    def test_new_install_generates_password(self):
        self.env.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            values = m.initialize(self.env)
        self.assertGreaterEqual(len(values['WEB_PASSWORD']), 16)
        self.assertEqual(m.config(self.env), values)

    def test_dotenv_is_data_not_shell_code(self):
        marker = self.root / 'must-not-exist'
        self.env.write_text(f'WEB_PASSWORD=$(touch {marker})-long-password\n')
        self.assertTrue(m.config(self.env)['WEB_PASSWORD'].startswith('$('))
        self.assertFalse(marker.exists())

    def test_invalid_ports_and_volume_collision(self):
        self.env.write_text('WEB_PASSWORD=long-enough-password\nWEB_PORT=65536\n')
        with self.assertRaises(RuntimeError):
            m.config(self.env)
        self.env.write_text('WEB_PASSWORD=long-enough-password\nWORKSPACE_VOLUME=same\nCODEX_HOME_VOLUME=same\n')
        with self.assertRaises(RuntimeError):
            m.config(self.env)

    def test_dry_run_has_no_docker_calls_or_password(self):
        with patch.object(m, 'docker', side_effect=AssertionError('Docker called')), contextlib.redirect_stdout(io.StringIO()) as output:
            m.deploy(self.values, self.env, dry_run=True)
        self.assertNotIn(self.values['WEB_PASSWORD'], output.getvalue())
        self.assertIn('--memory=256m', output.getvalue())
        self.assertIn('--memory 384m', output.getvalue())

    def test_repeated_deploy_does_not_rebuild_or_replace(self):
        with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=container()), patch.object(m, 'docker') as docker, patch.object(m, 'wait_healthy'), contextlib.redirect_stdout(io.StringIO()):
            m.deploy(self.values, self.env)
        docker.assert_not_called()

    def test_start_migrates_old_policy_and_starts_existing_container(self):
        with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=container(running=False, restart='unless-stopped')), patch.object(m, 'docker') as docker, patch.object(m, 'wait_healthy') as healthy, contextlib.redirect_stdout(io.StringIO()):
            m.start(self.values)
        self.assertEqual([c.args for c in docker.call_args_list], [('update', '--restart=no', 'codex-web'), ('start', 'codex-web')])
        healthy.assert_called_once_with('codex-web')

    def test_start_running_container_is_idempotent(self):
        with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=container()), patch.object(m, 'docker') as docker, patch.object(m, 'wait_healthy'), contextlib.redirect_stdout(io.StringIO()):
            m.start(self.values)
        docker.assert_not_called()

    def test_stop_disables_restart_before_stopping(self):
        with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=container(restart='always')), patch.object(m, 'docker') as docker, contextlib.redirect_stdout(io.StringIO()):
            m.stop(self.values)
        self.assertEqual([c.args for c in docker.call_args_list], [('update', '--restart=no', 'codex-web'), ('stop', '--timeout', '15', 'codex-web')])

    def test_stop_stopped_container_is_idempotent(self):
        with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=container(running=False)), patch.object(m, 'docker') as docker, contextlib.redirect_stdout(io.StringIO()):
            m.stop(self.values)
        docker.assert_not_called()

    def test_start_does_not_deploy_missing_container(self):
        with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=None), patch.object(m, 'docker') as docker:
            with self.assertRaisesRegex(RuntimeError, 'not found'):
                m.start(self.values)
        docker.assert_not_called()

    def test_manual_actions_do_not_touch_unrelated_container(self):
        item = container(managed=False)
        item['Config']['Image'] = 'another-service:latest'
        for action in [m.start, m.stop]:
            with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=item), patch.object(m, 'docker') as docker:
                with self.assertRaises(RuntimeError):
                    action(self.values)
            docker.assert_not_called()

    def test_new_container_never_automatically_restarts(self):
        args = m.run_command(self.values, self.env)
        self.assertEqual(args[args.index('--restart') + 1], 'no')

    def test_existing_deploy_disables_legacy_restart(self):
        with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=container(restart='unless-stopped')), patch.object(m, 'docker') as docker, patch.object(m, 'wait_healthy'), contextlib.redirect_stdout(io.StringIO()):
            m.deploy(self.values, self.env)
        docker.assert_called_once_with('update', '--restart=no', 'codex-web')

    def test_unrelated_container_is_not_touched(self):
        item = container(managed=False)
        item['Config']['Image'] = 'another-service:latest'
        with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=item), patch.object(m, 'docker') as docker:
            with self.assertRaises(RuntimeError):
                m.deploy(self.values, self.env, replace=True)
        docker.assert_not_called()

    def fake_deploy(self, fail_health=False, fail_build=False, fail_rename=False):
        state = {'codex-web': container()}
        calls = []

        def docker(*args, **kwargs):
            calls.append(args)
            if args[0] == 'build' and fail_build:
                raise subprocess.CalledProcessError(1, ['docker', 'build'])
            if args[0] == 'rename':
                if fail_rename:
                    raise subprocess.CalledProcessError(1, ['docker', 'rename'])
                state[args[2]] = state.pop(args[1])
            elif args[0] == 'stop':
                state[args[-1]]['State']['Running'] = False
            elif args[0] == 'start':
                state[args[-1]]['State']['Running'] = True
            elif args[0] == 'rm':
                state.pop(args[-1], None)
            elif args[0] == 'run':
                state['codex-web'] = container()
                state['codex-web']['new'] = True
            return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

        def healthy(name):
            if fail_health and state[name].get('new'):
                raise RuntimeError('health failed')

        with patch.object(m, 'preflight'), patch.object(m, 'inspect', side_effect=lambda name: state.get(name)), patch.object(m, 'docker', side_effect=docker), patch.object(m, 'wait_healthy', side_effect=healthy), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if fail_build or fail_health or fail_rename:
                with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                    m.deploy(self.values, self.env, replace=True)
            else:
                m.deploy(self.values, self.env, replace=True)
        return state, calls

    def test_build_failure_keeps_running_container(self):
        state, calls = self.fake_deploy(fail_build=True)
        self.assertTrue(state['codex-web']['State']['Running'])
        self.assertFalse(any(c[0] == 'stop' for c in calls))

    def test_failed_health_restores_previous_container(self):
        state, calls = self.fake_deploy(fail_health=True)
        self.assertEqual(list(state), ['codex-web'])
        self.assertTrue(state['codex-web']['State']['Running'])
        self.assertNotIn('new', state['codex-web'])
        self.assertTrue(any(c[0] == 'rm' for c in calls))

    def test_failed_rename_restarts_original(self):
        state, calls = self.fake_deploy(fail_rename=True)
        self.assertTrue(state['codex-web']['State']['Running'])

    def test_successful_update_keeps_stopped_previous(self):
        state, calls = self.fake_deploy()
        self.assertTrue(state['codex-web']['new'])
        old = [name for name in state if name != 'codex-web']
        self.assertEqual(len(old), 1)
        self.assertFalse(state[old[0]]['State']['Running'])
        self.assertLess(next(i for i, c in enumerate(calls) if c[0] == 'build'), next(i for i, c in enumerate(calls) if c[0] == 'stop'))

    def test_backup_failure_restarts_container(self):
        with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=container()), patch.object(m, 'docker') as docker, patch.object(m, 'helper', side_effect=RuntimeError('tar failed')):
            with self.assertRaises(RuntimeError):
                m.backup(self.values, self.env, self.root / 'backup')
        self.assertEqual(docker.call_args_list[-1].args, ('start', 'codex-web'))
        self.assertFalse((self.root / 'backup/manifest.json').exists())

    def fixture_backup(self, unsafe=False):
        directory = self.root / 'fixture'
        directory.mkdir()
        for name in ('workspace.tar.gz', 'codex-home.tar.gz'):
            with tarfile.open(directory / name, 'w:gz') as archive:
                member = tarfile.TarInfo('../escape' if unsafe else 'sample.txt')
                member.size = 2
                archive.addfile(member, io.BytesIO(b'ok'))
        (directory / '.env').write_bytes(self.env.read_bytes())
        (directory / 'manifest.json').write_text(json.dumps({'format': 1, 'files': {name: m.sha256(directory / name) for name in ('workspace.tar.gz', 'codex-home.tar.gz', '.env')}}))
        return directory

    def test_corrupt_backup_rejected_before_docker(self):
        directory = self.fixture_backup()
        (directory / 'workspace.tar.gz').write_bytes(b'corrupted')
        with patch.object(m, 'preflight') as preflight:
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                m.restore(self.values, self.env, directory, False)
        preflight.assert_not_called()

    def test_path_traversal_in_backup_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Unsafe backup'):
            m.verify_backup(self.fixture_backup(unsafe=True))

    def test_restore_refuses_nonempty_volumes(self):
        directory = self.fixture_backup()
        with patch.object(m, 'preflight'), patch.object(m, 'inspect', return_value=None), patch.object(m, 'docker', return_value=SimpleNamespace(returncode=0)) as docker, patch.object(m, 'helper', return_value=SimpleNamespace(stdout=b'/data/project\n')):
            with self.assertRaisesRegex(RuntimeError, 'nonempty'):
                m.restore(self.values, self.env, directory, False)
        self.assertFalse(any(call.args[:2] == ('volume', 'create') for call in docker.call_args_list))

    def test_restore_does_not_replace_existing_env(self):
        directory = self.fixture_backup()
        before = self.env.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'absent'):
            m.restore(self.values, self.env, directory, True)
        self.assertEqual(self.env.read_bytes(), before)


class ExportTests(unittest.TestCase):
    def test_archive_excludes_credentials_and_has_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'project'
            root.mkdir()
            (root / 'README.md').write_text('public documentation')
            (root / '.env').write_text('WEB_PASSWORD=very-secret-password\n')
            (root / '.env.example').write_text('WEB_PASSWORD=\n')
            (root / 'scripts').mkdir()
            (root / 'scripts/auth.json').write_text('secret-token')
            (root / 'scripts/example.py').write_text('print("hello")')
            with patch.object(exporter, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                output = Path(directory) / 'source.tar.gz'
                exporter.export(output)
            with tarfile.open(output) as archive:
                names = archive.getnames()
            self.assertIn('codex-web/.env.example', names)
            self.assertIn('codex-web/SHA256SUMS', names)
            self.assertNotIn('codex-web/.env', names)
            self.assertNotIn('codex-web/scripts/auth.json', names)

    def test_live_password_in_source_prevents_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'project'
            root.mkdir()
            (root / '.env').write_text('WEB_PASSWORD=real-password-value\n')
            (root / 'README.md').write_text('real-password-value')
            with patch.object(exporter, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'password found'):
                    exporter.export(Path(directory) / 'source.tar.gz')


if __name__ == '__main__':
    unittest.main()
