import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from services.diagnostic_cases import import_report


class TestDiagnosticCases(unittest.TestCase):
    def test_duplicate_samples_are_not_counted_twice_and_old_samples_expire(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'cases.sqlite'
            report = {'observed_at': '2026-10-24T12:00:00+00:00',
                      'server': 'test', 'windows': []}
            self.assertTrue(import_report(report, db, max_samples=2)['inserted'])
            self.assertFalse(import_report(report, db, max_samples=2)['inserted'])
            for hour in (13, 14):
                report['observed_at'] = f'2026-10-24T{hour}:00:00+00:00'
                result = import_report(report, db, max_samples=2)
            self.assertEqual(result['samples'], 2)
            with closing(sqlite3.connect(db)) as conn:
                dates = [row[0] for row in conn.execute('SELECT observed_at FROM samples')]
            self.assertNotIn('2026-10-24T12:00:00+00:00', dates)

    def test_raw_logs_and_unrecognized_fields_are_not_stored(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'cases.sqlite'
            report = {
                'observed_at': '2026-10-24T12:00:00+00:00', 'server': 'test',
                'raw_log': 'do-not-store', 'windows': [{
                    'source': 'access', 'lines': 1, 'cookies': 'do-not-store',
                    'buckets': [{'kind': '/read', 'status': '200', 'count': 1,
                                 'body': 'do-not-store'}],
                }],
            }
            import_report(report, db)
            with closing(sqlite3.connect(db)) as conn:
                payload = conn.execute('SELECT report FROM samples').fetchone()[0]
            self.assertNotIn('do-not-store', payload)
            self.assertEqual(json.loads(payload)['windows'][0]['buckets'][0]['status'], '200')
