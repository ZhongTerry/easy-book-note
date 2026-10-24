"""Bounded local library of sanitized diagnostic samples, not raw server logs.

Import a JSON report with ``python -m services.diagnostic_cases --report <file>``.
HTTP success is transport evidence only, never proof of readable content.
"""
import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def import_report(report, db_path, max_samples=30):
    """Retain at most 30 reports and 100 classified groups per report.

    Only allowlisted fields are stored. No raw lines, query strings, credentials,
    reader content or client addresses belong in this report format.
    """
    if max_samples < 1:
        raise ValueError('max_samples must be positive')
    observed = str(report['observed_at'])[:64]
    datetime.fromisoformat(observed)
    server = str(report['server'])[:80]
    windows = []
    for window in report.get('windows', [])[:10]:
        windows.append({
            'source': str(window['source'])[:80],
            'lines': max(0, int(window['lines'])),
            'first': str(window.get('first') or '')[:64],
            'last': str(window.get('last') or '')[:64],
            'buckets': [{
                'kind': str(item['kind'])[:120],
                'status': str(item['status'])[:32],
                'host': str(item.get('host') or '')[:253],
                'count': max(0, int(item['count'])),
            } for item in window.get('buckets', [])[:10]],
        })
    clean = {'observed_at': observed, 'server': server, 'windows': windows}
    payload = json.dumps(clean, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('CREATE TABLE IF NOT EXISTS samples ('
                     'id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, '
                     'observed_at TEXT NOT NULL, imported_at TEXT NOT NULL, '
                     'server TEXT NOT NULL, report TEXT NOT NULL)')
        cursor = conn.execute(
            'INSERT OR IGNORE INTO samples '
            '(fingerprint, observed_at, imported_at, server, report) VALUES (?, ?, ?, ?, ?)',
            (digest, observed, datetime.now(timezone.utc).isoformat(), server, payload),
        )
        inserted = cursor.rowcount == 1
        conn.execute('DELETE FROM samples WHERE id NOT IN '
                     '(SELECT id FROM samples ORDER BY id DESC LIMIT ?)', (max_samples,))
        count = conn.execute('SELECT COUNT(*) FROM samples').fetchone()[0]
    return {'inserted': inserted, 'samples': count, 'database': str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, help='Sanitized JSON report file')
    parser.add_argument('--database', default='user_data/diagnostics/cases.sqlite')
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding='utf-8'))
    print(json.dumps(import_report(report, args.database), ensure_ascii=False))


if __name__ == '__main__':
    main()
