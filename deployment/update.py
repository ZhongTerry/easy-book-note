#!/usr/bin/env python3
"""Transactional in-place updater. Default: preview only. Python standard library.

Runtime data is never checked out, deleted or restored by a code rollback.
Service lifecycle commands are explicit argv arrays, never shell commands.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4


RUNTIME_DIRS = {'.git', '.venv', 'venv', 'env', 'cache', 'library', 'downloads',
                'debug', 'backups', 'node_modules'}
RUNTIME_FILES = {'gunicorn_conf.py', 'uwsgi.ini', '.htaccess', 'smart_pull.sh',
                 'fanqie_session.json', 'user_token.json', 'stats.json'}


def protected(name):
    parts = PurePosixPath(name).parts
    return any(p in RUNTIME_DIRS or p.startswith('user_data') for p in parts) or any(
        p.startswith(('.env', 'config.env', 'debug.')) or p in RUNTIME_FILES
        or p.endswith(('.db', '.sqlite', '.sqlite-wal', '.sqlite-shm', '.log', '.pid'))
        for p in parts
    )


def git(root, *args, timeout=120, binary=False):
    result = subprocess.run(['git', '-C', str(root), *args], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return result.stdout if binary else result.stdout.decode('utf-8', errors='replace').strip()


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp, path)
    if os.name == 'posix':
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def safe_path(root, name):
    parts = PurePosixPath(name).parts
    if not parts or name.startswith('/') or any(p in ('..', '.', '') for p in parts) or '\\' in name:
        raise ValueError(f'Unsafe archive path: {name!r}')
    path = root.joinpath(*parts)
    # Reject symlinks even if they currently point within the target: subsequent
    # writes must not follow mutable indirections outside the managed code tree.
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'Symlink in deployment path: {name}')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes deployment root: {name}')
    return path


def validate_config(config):
    root = Path(config['target']).resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor) or root == Path.home():
        raise ValueError('target must be an explicit application directory')
    if Path(git(root, 'rev-parse', '--show-toplevel')).resolve() != root:
        raise ValueError('target must be the Git repository root')
    branch = config['branch']
    git(root, 'check-ref-format', '--branch', branch)
    if git(root, 'branch', '--show-current') != branch:
        raise ValueError('Configured branch does not match the current branch')
    state = Path(config['state_dir']).resolve()
    if state == root or state.is_relative_to(root) or root.is_relative_to(state):
        raise ValueError('state_dir must be a separate sibling directory outside target')
    keep = config.get('keep_releases', 3)
    if not isinstance(keep, int) or not 1 <= keep <= 10:
        raise ValueError('keep_releases must be between 1 and 10')
    url = urlsplit(config['health_url'])
    if url.scheme not in ('http', 'https') or url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('health_url must use a loopback HTTP(S) address')
    if url.username or url.password or url.query or url.fragment:
        raise ValueError('health_url must not contain credentials, query or fragment')
    for name in ('stop_command', 'start_command'):
        command = config[name]
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise ValueError(f'{name} must be a nonempty argv array')
    return root, state


def tracked_code(root, revision):
    return {name for name in git(root, 'ls-tree', '-r', '--name-only', '-z', revision,
                                binary=True).decode('utf-8').split('\0')
            if name and not protected(name)}


def prepare(root, stage, revision):
    """Extract only regular code files; never execute the candidate application."""
    archive = git(root, 'archive', '--format=tar', revision, binary=True)
    files = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            safe_path(stage, member.name)
            if protected(member.name) or member.isdir():
                continue
            if not member.isfile():
                raise ValueError(f'Only regular files may be deployed: {member.name}')
            total += member.size
            if total > 256 * 1024 * 1024:
                raise ValueError('Candidate code exceeds the 256 MiB safety limit')
            path = safe_path(stage, member.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, path.open('wb') as target:
                shutil.copyfileobj(source, target)
            path.chmod(member.mode & 0o777)
            files.add(member.name)
            if path.suffix == '.py':
                # compile() checks syntax without imports or __pycache__ writes.
                compile(path.read_bytes(), member.name, 'exec')
    if not {'dbserver.py', 'requirements.txt'}.issubset(files):
        raise ValueError('Candidate is missing dbserver.py or requirements.txt')
    return files, total


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def make_plan(root, stage, old_revision, new_revision, files):
    previous = tracked_code(root, old_revision)
    # Runtime-only changes are allowed, but tracked code modifications are not.
    dirty = git(root, 'diff', 'HEAD', '--name-only', '-z', binary=True).decode('utf-8').split('\0')
    dirty = [name for name in dirty if name and not protected(name)]
    if dirty:
        raise ValueError('Local code changes must be reviewed first: ' + ', '.join(dirty[:10]))
    added, changed, removed = [], [], []
    for name in sorted(previous | files):
        path = safe_path(root, name)
        if name in files:
            if path.exists() and not path.is_file():
                raise ValueError(f'File/directory layout conflict: {name}')
            for parent in path.parents:
                if parent == root:
                    break
                if parent.exists() and not parent.is_dir():
                    raise ValueError(f'File/directory layout conflict: {name}')
        if name in files and name not in previous:
            if path.exists():
                raise ValueError(f'Candidate would overwrite an untracked path: {name}')
            added.append(name)
        elif name not in files:
            removed.append(name)
        elif digest(path) != digest(safe_path(stage, name)):
            changed.append(name)
    return {'old_revision': old_revision, 'new_revision': new_revision,
            'add': added, 'change': changed, 'remove': removed,
            'requirements_changed': 'requirements.txt' in changed,
            'protected_runtime_paths': 'user_data*, cache, library, downloads, config, logs, database, service configs'}


def lifecycle(config, name, root, log):
    log(f'SERVICE: {name}')
    result = subprocess.run(config[name], cwd=root, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=config.get('command_timeout', 120))
    # Do not persist command arguments/output: lifecycle tools may print secrets.
    if result.returncode:
        raise RuntimeError(f'{name} failed with exit code {result.returncode}; inspect the service manager')


def assert_stopped(url):
    parsed = urlsplit(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)), timeout=1):
            raise RuntimeError('The service port is still listening after stop_command')
    except ConnectionRefusedError:
        return


def healthy(url, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                body = json.loads(response.read(16384))
                if response.status == 200 and body.get('status') == 'ok':
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(1)
    return False


def backup_databases(root, destination):
    """Use SQLite backup API, including committed WAL data. No raw live copying."""
    for data_dir in root.glob('user_data*'):
        if not data_dir.is_dir() or data_dir.is_symlink():
            continue
        for path in data_dir.rglob('*'):
            if path.suffix not in ('.db', '.sqlite') or not path.is_file():
                continue
            safe_path(root, path.relative_to(root).as_posix())
            target = destination / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source, \
                 contextlib.closing(sqlite3.connect(target)) as backup:
                source.backup(backup)
                if backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError(f'SQLite backup integrity check failed: {path.name}')


def database_schemas(root):
    result = {}
    for directory in root.glob('user_data*'):
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in directory.rglob('*'):
            if path.suffix not in ('.db', '.sqlite') or not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            safe_path(root, name)
            with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
                schema = conn.execute('SELECT type, name, sql FROM sqlite_master ORDER BY type, name').fetchall()
            result[name] = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
    return result


def copy_code(source, root, names):
    for name in names:
        path = safe_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement per file; application is stopped for the full set.
        candidate = safe_path(source, name)
        fd, temp = tempfile.mkstemp(prefix='.notedb-update-', dir=path.parent)
        os.close(fd)
        try:
            shutil.copy2(candidate, temp)
            if path.exists() and hasattr(os, 'chown'):
                st = path.stat()
                if os.geteuid() == 0:
                    os.chown(temp, st.st_uid, st.st_gid)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


def restore(root, release, manifest):
    copy_code(release / 'code', root, manifest['saved_files'])
    for name in manifest['added_files']:
        path = safe_path(root, name)
        if path.is_file():
            path.unlink()
    git(root, 'reset', '--mixed', '--quiet', manifest['old_revision'])


def apply(root, state, stage, plan, config, log):
    if plan['requirements_changed'] and not config.get('dependencies_reviewed'):
        raise ValueError('requirements.txt changed: review/install dependencies separately before deployment')
    needed = sum((root / p).stat().st_size for p in plan['change'] + plan['remove'] if (root / p).is_file())
    needed += sum(p.stat().st_size for p in root.glob('user_data*/**/*') if p.is_file() and p.suffix in ('.sqlite', '.db'))
    if shutil.disk_usage(state).free < needed * 2 + 64 * 1024 * 1024:
        raise ValueError('Insufficient disk space for code and SQLite backup')
    release = state / ('release-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8])
    release.mkdir(mode=0o700)
    saved = [p for p in plan['change'] + plan['remove'] if (root / p).is_file()]
    manifest = {**plan, 'saved_files': saved, 'added_files': plan['add'],
                'target': str(root), 'status': 'prepared'}
    for name in saved:
        src = safe_path(root, name)
        dst = release / 'code' / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    atomic_json(release / 'manifest.json', manifest)
    stopped = False
    wrote_code = False
    try:
        log(f'BACKUP: prepared {release.name}; stopping application')
        stopped = True  # Even a partially failed stop must try to recover service.
        lifecycle(config, 'stop_command', root, log)
        assert_stopped(config['health_url'])
        # Recheck after shutdown: refuse a deploy if code was changed mid-flight.
        if git(root, 'rev-parse', 'HEAD') != plan['old_revision']:
            raise RuntimeError('Git HEAD changed during preparation')
        files = {p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
        make_plan(root, stage, plan['old_revision'], plan['new_revision'], files)
        backup_databases(root, release / 'databases')
        manifest['database_schemas'] = database_schemas(root)
        log('BACKUP: SQLite snapshots verified; runtime data will remain in place')
        manifest['status'] = 'applying'
        atomic_json(release / 'manifest.json', manifest)
        wrote_code = True
        copy_code(stage, root, plan['add'] + plan['change'])
        for name in plan['remove']:
            path = safe_path(root, name)
            if path.is_file():
                path.unlink()
        # Mixed reset changes Git metadata only; protected runtime files are
        # never reset/cleaned or overwritten, even when tracked by old commits.
        git(root, 'reset', '--mixed', '--quiet', plan['new_revision'])
        lifecycle(config, 'start_command', root, log)
        log('HEALTH: waiting for application readiness')
        if not healthy(config['health_url'], config.get('health_timeout', 45)):
            raise RuntimeError('Candidate failed its JSON health check')
        manifest['status'] = 'succeeded'
        atomic_json(release / 'manifest.json', manifest)
        log(f'SUCCESS: {plan["new_revision"]}; rollback snapshot {release.name}')
        return release
    except Exception as exc:
        log(f'FAILED: {type(exc).__name__}: {exc}')
        manifest['status'] = 'failed'
        atomic_json(release / 'manifest.json', manifest)
        if stopped:
            if wrote_code:
                lifecycle(config, 'stop_command', root, log)
                assert_stopped(config['health_url'])
                if database_schemas(root) != manifest['database_schemas']:
                    manifest['status'] = 'needs_manual_recovery'
                    atomic_json(release / 'manifest.json', manifest)
                    raise RuntimeError(
                        f'Database schema changed: service remains stopped; inspect {release}. '
                        'No database was overwritten and old code was not started.'
                    ) from exc
                restore(root, release, manifest)
            lifecycle(config, 'start_command', root, log)
            if not healthy(config['health_url'], config.get('health_timeout', 45)):
                raise RuntimeError(f'Rollback/recovery health check failed; inspect {release}') from exc
            manifest['status'] = 'rolled_back' if wrote_code else 'recovered'
            atomic_json(release / 'manifest.json', manifest)
            log('RECOVERED: original code serving; database was not restored')
        raise


def recover(root, state, release_name, config, log):
    if not release_name.startswith('release-') or Path(release_name).name != release_name:
        raise ValueError('Recovery requires an exact release directory name')
    release = state / release_name
    if release.is_symlink():
        raise ValueError('Recovery directory must not be a symlink')
    manifest = json.loads((release / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['target'] != str(root):
        raise ValueError('Recovery snapshot belongs to a different application')
    if git(root, 'rev-parse', 'HEAD') not in (manifest['old_revision'], manifest['new_revision']):
        raise ValueError('A different revision is active; refusing to apply an unrelated recovery snapshot')
    for name in manifest['saved_files']:
        if not safe_path(release / 'code', name).is_file() or protected(name):
            raise ValueError('Recovery snapshot is missing code or references runtime data')
    for name in manifest['added_files']:
        if protected(name):
            raise ValueError('Recovery manifest references runtime data')
    lifecycle(config, 'stop_command', root, log)
    assert_stopped(config['health_url'])
    if manifest.get('database_schemas') is not None and database_schemas(root) != manifest['database_schemas']:
        raise RuntimeError('Database schema changed; recovery requires a manual compatibility review')
    restore(root, release, manifest)
    lifecycle(config, 'start_command', root, log)
    if not healthy(config['health_url'], config.get('health_timeout', 45)):
        raise RuntimeError('Recovery health check failed; inspect service logs')
    manifest['status'] = 'rolled_back'
    atomic_json(release / 'manifest.json', manifest)
    log('RECOVERED: old code restored; database and user files were not restored')


def _run_main(argv, runtime):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Private deployment JSON config')
    parser.add_argument('--apply', action='store_true', help='Stop service and apply the previewed revision')
    parser.add_argument('--revision', help='Required with --apply; full commit SHA approved in preview')
    parser.add_argument('--fetch', action='store_true', help='Fetch configured branch from origin before preview')
    parser.add_argument('--dependencies-reviewed', action='store_true', help='Dependencies for this revision were separately installed and reviewed')
    parser.add_argument('--recover', help='Recover an interrupted update by exact release directory name; requires --apply')
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    root, state = validate_config(config)
    config['dependencies_reviewed'] = args.dependencies_reviewed
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Do not allow concurrent deployment from a second process (Linux only).
    import fcntl
    with (state / 'update.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another update is in progress')
        def log(message):
            runtime['phase'] = message.split(':', 1)[0]
            line = time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + message
            print(line, flush=True)
            # Bound the operational log independently of backup retention.
            path = state / 'update.log'
            if path.exists() and path.stat().st_size > 1024 * 1024:
                os.replace(path, state / 'update.log.1')
            with path.open('a', encoding='utf-8') as output:
                output.write(line + '\n')
        runtime.update(state=state, log=log)
        if args.recover:
            if not args.apply:
                raise ValueError('--recover requires --apply; it stops and restarts the application')
            recover(root, state, args.recover, config, log)
            return 0
        incomplete = [p.parent.name for p in state.glob('release-*/manifest.json')
                      if json.loads(p.read_text(encoding='utf-8'))['status'] not in
                      ('succeeded', 'rolled_back', 'recovered')]
        if incomplete:
            raise RuntimeError('Unfinished update requires inspection/recovery: ' + ', '.join(incomplete))
        old = git(root, 'rev-parse', 'HEAD')
        if args.fetch:
            log('FETCH: origin/' + config['branch'])
            git(root, 'fetch', '--no-tags', 'origin', 'refs/heads/' + config['branch'])
        revision = args.revision or ('FETCH_HEAD' if args.fetch else 'origin/' + config['branch'])
        new = git(root, 'rev-parse', '--verify', '--end-of-options', revision + '^{commit}')
        if args.apply and (not args.revision or args.revision != new):
            raise ValueError('--apply requires the full --revision SHA from a preview')
        with tempfile.TemporaryDirectory(prefix='stage-', dir=state) as temp:
            stage = Path(temp)
            files, size = prepare(root, stage, new)
            plan = make_plan(root, stage, old, new, files)
            log(f'PLAN: {old[:12]} -> {new[:12]}; +{len(plan["add"])} ~{len(plan["change"])} -{len(plan["remove"])}; candidate {size} bytes')
            atomic_json(state / 'last-plan.json', plan)
            if not args.apply:
                print(json.dumps(plan, ensure_ascii=False, indent=2))
                log('PREVIEW ONLY: no service restart or application file changes')
                return 0
            if not any(plan[key] for key in ('add', 'change', 'remove')) and old == new:
                log('UP TO DATE: no changes needed')
                return 0
            if not healthy(config['health_url'], 5):
                raise RuntimeError('Current application is not healthy; no deployment was started')
            apply(root, state, stage, plan, config, log)
            releases = sorted(state.glob('release-*/manifest.json'), key=lambda p: p.parent.name, reverse=True)
            # Retain recent successful/rolled-back snapshots; never delete
            # failed/incomplete releases, which may be needed for recovery.
            complete = [p for p in releases if json.loads(p.read_text(encoding='utf-8'))['status'] in ('succeeded', 'rolled_back', 'recovered')]
            for path in complete[config.get('keep_releases', 3):]:
                directory = path.parent.resolve()
                if directory.parent == state and directory.name.startswith('release-'):
                    shutil.rmtree(directory)
    return 0


def main(argv=None):
    runtime = {}
    try:
        return _run_main(argv, runtime)
    except Exception as exc:
        if 'state' in runtime:
            failure = {'phase': runtime.get('phase', 'PRECHECK'),
                       'type': type(exc).__name__, 'message': str(exc)}
            try:
                atomic_json(runtime['state'] / 'last-error.json', failure)
                runtime['log'](f'ABORTED: {failure["type"]}: {failure["message"]}')
            except OSError:
                pass  # Preserve the original failure if disk/logging also failed.
        raise


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'UPDATE ABORTED: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
