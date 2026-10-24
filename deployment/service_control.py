#!/usr/bin/env python3
"""Lifecycle adapter for the existing standalone Gunicorn deployment.

Never discovers/kills processes by name. The configured PID must belong to the
expected application directory and be Gunicorn before a signal is sent.
"""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def verify_process(pid, root):
    if pid <= 1:
        raise ValueError('Refusing an invalid/master system PID')
    proc = Path('/proc') / str(pid)
    if not proc.exists():
        return False
    if (proc / 'cwd').resolve() != root:
        raise ValueError('PID belongs to a different application directory')
    args = (proc / 'cmdline').read_bytes().split(b'\0')
    if not any(b'gunicorn' in arg for arg in args):
        raise ValueError('PID is not a verified Gunicorn process')
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop'))
    parser.add_argument('--target', required=True)
    parser.add_argument('--pidfile', required=True)
    parser.add_argument('--gunicorn')
    parser.add_argument('--config')
    parser.add_argument('--timeout', type=int, default=45)
    args = parser.parse_args(argv)
    root = Path(args.target).resolve(strict=True)
    pidfile = Path(args.pidfile).resolve()
    if pidfile.parent != root:
        raise ValueError('PID file must be directly inside target')
    pid = int(pidfile.read_text().strip()) if pidfile.exists() else None
    running = pid is not None and verify_process(pid, root)
    if args.action == 'stop':
        if not running:
            print('ALREADY STOPPED')
            return 0
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if not Path(f'/proc/{pid}').exists():
                print('STOPPED')
                return 0
            time.sleep(0.25)
        raise RuntimeError('Graceful stop timed out; no SIGKILL was sent')
    if running:
        print('ALREADY RUNNING')
        return 0
    if not args.gunicorn or not args.config:
        raise ValueError('start requires explicit --gunicorn and --config')
    executable = Path(args.gunicorn).resolve(strict=True)
    config = Path(args.config).resolve(strict=True)
    if config.parent != root:
        raise ValueError('Gunicorn config must be inside target')
    # Gunicorn owns its PID and logging according to the existing site config.
    # Its master is daemonized, so this helper does not remain a service parent.
    subprocess.run([str(executable), '--daemon', '-c', str(config), 'dbserver:app'],
                   cwd=root, check=True, timeout=args.timeout)
    print('START REQUESTED; updater will verify readiness')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'SERVICE CONTROL FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
