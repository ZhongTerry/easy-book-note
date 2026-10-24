"""Offline tests for deployment/update.py using temporary Git repositories.

These exercise the file-level primitives (prepare/make_plan/apply) without the
Linux-only flock in main(), real service commands, sockets, or network access.
"""
import contextlib
import importlib.util
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


UPDATE_PATH = Path(__file__).resolve().parents[1] / 'deployment' / 'update.py'
_spec = importlib.util.spec_from_file_location('notedb_deployment_update', UPDATE_PATH)
update = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update)

OLD_CODE = b'print("old")\n'
NEW_CODE = b'print("new")\n'


class DeploymentUpdateTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.repo = self.base / 'app'
        self.repo.mkdir()
        self.state = self.base / 'state'
        self.state.mkdir()
        self.stage = self.base / 'stage'
        self.stage.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Deployment Test')
        self.git('config', 'user.email', 'deployment@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'core.autocrlf', 'false')

    def git(self, *args, input=None):
        return subprocess.run(
            ['git', '-C', str(self.repo), *args],
            check=True,
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def revision(self):
        return self.git('rev-parse', 'HEAD').stdout.decode().strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)

    def commit(self, files, message='test commit'):
        for name, content in files.items():
            self.write(name, content)
        self.git('add', '-f', '-A')
        self.git('commit', '-q', '-m', message)
        return self.revision()

    def config(self):
        return {
            'health_url': 'http://127.0.0.1:9/healthz',
            'stop_command': ['fake-stop'],
            'start_command': ['fake-start'],
            'command_timeout': 1,
            'health_timeout': 1,
        }

    def prepare_candidate(self, revision):
        return update.prepare(self.repo, self.stage, revision)

    def test_prepare_excludes_tracked_runtime_paths_from_code_plan(self):
        old = self.commit({
            'dbserver.py': 'print("old")\n',
            'requirements.txt': 'flask\n',
            'gunicorn_conf.py': 'bind = "old"\n',
            'cache/state.txt': 'runtime\n',
            'user_data/notes.db': 'not-a-real-database',
            '.env': 'SECRET=1\n',
        })
        new = self.commit({
            'dbserver.py': 'print("new")\n',
            'gunicorn_conf.py': 'bind = "new"\n',
        }, message='candidate')
        self.git('reset', '--hard', '--quiet', old)

        files, _ = self.prepare_candidate(new)
        previous = update.tracked_code(self.repo, old)
        plan = update.make_plan(self.repo, self.stage, old, new, files)

        self.assertEqual(files, {'dbserver.py', 'requirements.txt'})
        self.assertEqual(previous, files)
        self.assertEqual(plan['change'], ['dbserver.py'])
        self.assertFalse((self.stage / 'gunicorn_conf.py').exists())
        self.assertFalse((self.stage / 'user_data').exists())
        self.assertFalse((self.stage / 'cache').exists())
        self.assertFalse((self.stage / '.env').exists())
        self.assertEqual((self.repo / 'gunicorn_conf.py').read_bytes(),
                         b'bind = "old"\n')

    def test_prepare_rejects_python_syntax_errors(self):
        self.commit({'dbserver.py': 'print("ok")\n', 'requirements.txt': 'flask\n'})
        self.commit({'dbserver.py': 'def broken(:\n'}, message='broken candidate')

        with self.assertRaises(SyntaxError):
            self.prepare_candidate('HEAD')

    def test_prepare_rejects_symlink_archive_entries(self):
        self.commit({'dbserver.py': 'print("ok")\n', 'requirements.txt': 'flask\n'})
        blob = self.git('hash-object', '-w', '--stdin', input=b'dbserver.py'
                        ).stdout.decode().strip()
        self.git('update-index', '--add', '--cacheinfo', f'120000,{blob},link.py')
        self.git('commit', '-q', '-m', 'symlink candidate')

        with self.assertRaisesRegex(ValueError, 'Only regular files'):
            self.prepare_candidate('HEAD')

    def test_make_plan_rejects_uncommitted_code_but_allows_runtime_changes(self):
        revision = self.commit({
            'dbserver.py': 'print("ok")\n',
            'requirements.txt': 'flask\n',
            'user_data/notes.db': 'runtime',
        })
        files, _ = self.prepare_candidate(revision)

        self.write('user_data/notes.db', 'changed runtime data')
        plan = update.make_plan(self.repo, self.stage, revision, revision, files)
        self.assertEqual(plan['change'], [])

        self.write('dbserver.py', 'print("uncommitted")\n')
        with self.assertRaisesRegex(ValueError, 'Local code changes.*dbserver.py'):
            update.make_plan(self.repo, self.stage, revision, revision, files)

    def test_make_plan_rejects_candidate_overwriting_untracked_file(self):
        old = self.commit({'dbserver.py': 'print("ok")\n', 'requirements.txt': 'flask\n'})
        new = self.commit({'newmodule.py': 'VALUE = 1\n'}, message='candidate')
        files, _ = self.prepare_candidate(new)
        self.git('reset', '--hard', '--quiet', old)
        self.write('newmodule.py', 'local work in progress')

        with self.assertRaisesRegex(ValueError, 'untracked path.*newmodule.py'):
            update.make_plan(self.repo, self.stage, old, new, files)

    def test_backup_databases_includes_committed_wal_data(self):
        data_dir = self.repo / 'user_data'
        data_dir.mkdir()
        db_path = data_dir / 'notes.db'
        writer = sqlite3.connect(db_path)
        try:
            self.assertEqual(writer.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute('CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)')
            writer.execute('INSERT INTO notes(body) VALUES (?)', ('written-to-wal',))
            writer.commit()
            self.assertTrue((data_dir / 'notes.db-wal').exists())

            destination = self.base / 'backup'
            update.backup_databases(self.repo, destination)

            backup_path = destination / 'user_data' / 'notes.db'
            with contextlib.closing(sqlite3.connect(backup_path)) as backup:
                rows = backup.execute('SELECT body FROM notes').fetchall()
            self.assertEqual(rows, [('written-to-wal',)])
        finally:
            writer.close()

    def _prepare_failing_update(self):
        old = self.commit({'dbserver.py': OLD_CODE.decode(), 'requirements.txt': 'flask\n'})
        new = self.commit({
            'dbserver.py': NEW_CODE.decode(),
            'feature.py': 'VALUE = 1\n',
        }, message='candidate')
        self.git('reset', '--hard', '--quiet', old)

        data_dir = self.repo / 'user_data'
        data_dir.mkdir()
        db_path = data_dir / 'live.db'
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute('CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)')
            conn.execute('INSERT INTO notes(body) VALUES (?)', ('old',))
            conn.commit()

        files, _ = self.prepare_candidate(new)
        plan = update.make_plan(self.repo, self.stage, old, new, files)
        return old, new, db_path, plan

    def _release_dir(self):
        releases = list(self.state.glob('release-*'))
        self.assertEqual(len(releases), 1)
        return releases[0]

    def test_health_failure_rolls_back_code_but_keeps_new_user_data(self):
        old, new, db_path, plan = self._prepare_failing_update()
        health_calls = []

        def fake_healthy(url, timeout=45):
            health_calls.append(url)
            if len(health_calls) == 1:
                with contextlib.closing(sqlite3.connect(db_path)) as conn:
                    conn.execute('INSERT INTO notes(body) VALUES (?)', ('new',))
                    conn.commit()
                return False
            return True

        with mock.patch.object(update, 'lifecycle') as lifecycle, \
             mock.patch.object(update, 'assert_stopped') as assert_stopped, \
             mock.patch.object(update, 'healthy', side_effect=fake_healthy):
            with self.assertRaisesRegex(RuntimeError, 'failed its JSON health check'):
                update.apply(self.repo, self.state, self.stage, plan,
                             self.config(), lambda message: None)

        self.assertEqual((self.repo / 'dbserver.py').read_bytes(), OLD_CODE)
        self.assertFalse((self.repo / 'feature.py').exists())
        self.assertEqual(self.revision(), old)
        self.assertEqual([call.args[1] for call in lifecycle.call_args_list],
                         ['stop_command', 'start_command', 'stop_command', 'start_command'])
        self.assertEqual(assert_stopped.call_count, 2)
        self.assertEqual(len(health_calls), 2)

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            self.assertEqual([row[0] for row in conn.execute(
                'SELECT body FROM notes ORDER BY id')], ['old', 'new'])

        release = self._release_dir()
        manifest = json.loads((release / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['status'], 'rolled_back')
        snapshot = release / 'databases' / 'user_data' / 'live.db'
        with contextlib.closing(sqlite3.connect(snapshot)) as conn:
            self.assertEqual([row[0] for row in conn.execute(
                'SELECT body FROM notes ORDER BY id')], ['old'])

    def test_schema_change_blocks_automatic_rollback(self):
        old, new, db_path, plan = self._prepare_failing_update()

        def fake_healthy(url, timeout=45):
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute('ALTER TABLE notes ADD COLUMN new_column TEXT')
                conn.commit()
            return False

        with mock.patch.object(update, 'lifecycle') as lifecycle, \
             mock.patch.object(update, 'assert_stopped') as assert_stopped, \
             mock.patch.object(update, 'healthy', side_effect=fake_healthy):
            with self.assertRaisesRegex(RuntimeError, 'Database schema changed'):
                update.apply(self.repo, self.state, self.stage, plan,
                             self.config(), lambda message: None)

        self.assertEqual((self.repo / 'dbserver.py').read_bytes(), NEW_CODE)
        self.assertTrue((self.repo / 'feature.py').exists())
        self.assertEqual(self.revision(), new)
        self.assertEqual([call.args[1] for call in lifecycle.call_args_list],
                         ['stop_command', 'start_command', 'stop_command'])
        self.assertEqual(assert_stopped.call_count, 2)

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            columns = [row[1] for row in conn.execute('PRAGMA table_info(notes)')]
            self.assertIn('new_column', columns)

        manifest = json.loads((self._release_dir() / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['status'], 'needs_manual_recovery')


if __name__ == '__main__':
    unittest.main()
