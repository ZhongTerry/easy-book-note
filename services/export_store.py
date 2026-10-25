"""Atomic export task snapshots with a cross-process SQLite mutex."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from contextlib import closing


def save_task(filename, task_id, task):
    path = Path(filename)
    # A dedicated SQLite file provides the same lock on Linux and Windows.
    # No task data is stored in this lock database.
    with closing(sqlite3.connect(str(path) + '.lock.sqlite', timeout=30)) as lock, lock:
        lock.execute('BEGIN IMMEDIATE')
        if path.exists():
            with path.open(encoding='utf-8') as source:
                tasks = json.load(source)
            if not isinstance(tasks, dict):
                raise ValueError('Export task file must contain a JSON object')
        else:
            tasks = {}
        tasks[task_id] = task
        # Serialize before touching the existing snapshot; malformed input cannot
        # truncate it. Unique temp files avoid writer collisions.
        payload = json.dumps(tasks, ensure_ascii=False, indent=2)
        fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
