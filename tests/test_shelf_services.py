"""自动备份与书架体检的测试。"""
import json
import os
import unittest
from contextlib import closing
from unittest.mock import Mock, patch

import managers
from services import backup as backup_service
from services import shelf_health


class BackupTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db_patch = patch.object(managers, 'DB_PATH',
                                os.path.join(self.temp.name, 't.sqlite'))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        managers.IsolatedDB()  # 在临时库上建表（单例是在真实路径上初始化的）
        self.backup_dir = os.path.join(self.temp.name, 'backups')


class TestLibrarySnapshot(BackupTestBase):
    def _seed_books(self):
        managers.db.save_raw_book('reader', 'bk1', {'value': {'url': 'http://s/1'}, 'tags': ['科幻']})
        managers.db.save_raw_book('reader', 'bk2', {'value': {'url': 'http://s/2'}})
        managers.booklist_manager.add_list('mylist', username='reader')

    def test_snapshot_creates_portable_backup(self):
        self._seed_books()
        filename, count = backup_service.create_snapshot('reader', self.backup_dir, keep=5)
        self.assertEqual(count, 2)
        data = json.load(open(os.path.join(self.backup_dir, filename), encoding='utf-8'))
        self.assertEqual(data['format'], 'notedb-library-backup')
        keys = {b['key'] for b in data['books']}
        self.assertEqual(keys, {'bk1', 'bk2'})
        for book in data['books']:
            self.assertEqual(book['cache'], {})  # 可再生缓存不入备份
        lists = data['booklists']
        self.assertTrue(any(l.get('name') == 'mylist' for l in lists.values()))

    def test_rotation_keeps_only_latest(self):
        self._seed_books()
        for _ in range(6):
            backup_service.create_snapshot('reader', self.backup_dir, keep=3)
            # 文件名精确到秒，强制推进时间避免同名覆盖
            import time as _time
            _time.sleep(1.05)
        files = [f for f in os.listdir(self.backup_dir) if f.endswith('.json')]
        self.assertEqual(len(files), 3)

    def test_snapshot_all_users(self):
        self._seed_books()
        managers.db.save_raw_book('other', 'bk9', {'value': {'url': 'http://s/9'}})
        results = backup_service.snapshot_all_users(self.backup_dir, keep=2)
        usernames = {r['username'] for r in results}
        self.assertEqual(usernames, {'reader', 'other'})


class TestShelfHealth(BackupTestBase):
    def _seed(self):
        managers.db.save_raw_book('reader', 'alive', {
            'value': {'url': 'http://alive.test/1/2.html', 'title': '活书',
                      'last_read_index': 2},
            'update_info': {'toc_url': 'http://alive.test/1/'},
        })
        managers.db.save_raw_book('reader', 'dead', {
            'value': {'url': 'http://dead.test/9/8.html', 'title': '死书',
                      'last_read_index': 8},
            'update_info': {'toc_url': 'http://dead.test/9/'},
        })
        managers.db.save_raw_book('reader', 'nourl', {'value': {}})

    def test_report_groups_by_status_and_domain(self):
        self._seed()

        def probe(url):
            if 'dead.test' in url:
                return {'ok': False, 'http': 404}
            return {'ok': True, 'http': 200}

        report = shelf_health.run_health_check('reader', probe=probe)
        by_key = {r['book_key']: r for r in report['results']}
        self.assertEqual(by_key['alive']['status'], 'reachable')
        self.assertEqual(by_key['dead']['status'], 'dead')
        self.assertEqual(by_key['nourl']['status'], 'no_url')
        domains = {d['domain']: d for d in report['domains']}
        self.assertEqual(domains['dead.test']['dead'], 1)
        self.assertEqual(domains['alive.test']['reachable'], 1)
        self.assertEqual(report['summary']['dead'], 1)

    def test_report_persisted_and_reloadable(self):
        self._seed()
        with patch.object(shelf_health, '_probe_factory', return_value=lambda u: {'ok': True, 'http': 200}):
            shelf_health.run_health_check('reader')
        loaded = shelf_health.load_latest_report()
        self.assertEqual(loaded['summary']['total'], 3)

    def test_probe_failure_is_unreachable_not_dead(self):
        self._seed()

        def probe(url):
            raise OSError('network down')

        report = shelf_health.run_health_check('reader', probe=probe)
        by_key = {r['book_key']: r for r in report['results']}
        self.assertEqual(by_key['alive']['status'], 'unreachable')
        self.assertEqual(by_key['dead']['status'], 'unreachable')


if __name__ == '__main__':
    unittest.main()


class TestPlanSwitches(BackupTestBase):
    def _seed_dead(self):
        managers.db.save_raw_book('reader', 'deadbook', {
            'value': {'url': 'http://dead.test/9/8.html', 'title': '死书',
                      'last_read_index': 8, 'last_read_title': '第八章'},
        })

    def test_plan_uses_search_and_reports_candidates(self):
        self._seed_dead()
        crawler = Mock()
        crawler.search_alternative_sources.return_value = [
            {'source': 'new.test', 'url': 'http://new.test/1/8.html',
             'title': '第八章', 'toc_url': 'http://new.test/1/'},
        ]
        result = shelf_health.plan_source_switches('reader', ['deadbook'], crawler)
        s = result['suggestions'][0]
        self.assertEqual(s['status'], 'found')
        self.assertEqual(s['suggested']['source'], 'new.test')
        self.assertEqual(result['summary']['found'], 1)
        crawler.search_alternative_sources.assert_called_once_with('死书', 8)

    def test_plan_reports_missing_title(self):
        managers.db.save_raw_book('reader', 'notitle', {'value': {'url': 'http://x/1.html'}})
        crawler = Mock()
        result = shelf_health.plan_source_switches('reader', ['notitle'], crawler)
        self.assertEqual(result['suggestions'][0]['status'], 'error')
        self.assertIn('书名', result['suggestions'][0]['reason'])

    def test_plan_survives_search_failure(self):
        self._seed_dead()
        crawler = Mock()
        crawler.search_alternative_sources.side_effect = RuntimeError('searx down')
        result = shelf_health.plan_source_switches('reader', ['deadbook'], crawler)
        self.assertEqual(result['suggestions'][0]['status'], 'not_found')
        self.assertEqual(result['summary']['found'], 0)

    def test_apply_switches_updates_url_and_clears_cache(self):
        managers.db.save_raw_book('reader', 'sw', {'value': {'url': 'http://old.test/1.html'}})
        managers.cache.set('http://old.test/1.html', {'stale': True})

        with patch('shared.is_safe_url', return_value=True):
            result = shelf_health.apply_switches('reader', [
                {'book_key': 'sw', 'new_url': 'http://new.test/1/8.html'},
            ])

        self.assertEqual(result['applied'], 1)
        self.assertIsNone(managers.cache.get('http://old.test/1.html'))
        self.assertEqual(managers.db.get_full_data('sw', 'reader')['url'],
                         'http://new.test/1/8.html')

    def test_apply_rejects_unsafe_url(self):
        managers.db.save_raw_book('reader', 'sw2', {'value': {'url': 'http://old.test/1.html'}})
        with patch('shared.is_safe_url', return_value=False):
            result = shelf_health.apply_switches('reader', [
                {'book_key': 'sw2', 'new_url': 'http://new.test/1/8.html'},
            ])
        self.assertEqual(result['applied'], 0)
        self.assertEqual(result['results'][0]['status'], 'error')


class TestShelfEndpoints(BackupTestBase):
    def _client(self):
        from flask import Flask
        from routes.core_bp import core_bp

        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='t')
        app.register_blueprint(core_bp)
        client = app.test_client()
        with client.session_transaction() as s:
            s['user'] = {'username': 'reader', 'csrf_token': 'tok'}
        return client

    @staticmethod
    def _assert_bindable(captured):
        """模拟 TaskManager 注入 callback 后的实参绑定，杜绝重复传参。"""
        import inspect
        sig = inspect.signature(captured['fn'])
        kwargs = dict(captured['kwargs'])
        kwargs.setdefault('callback', None)
        sig.bind(*captured['args'], **kwargs)

    def test_health_check_task_receives_username_once(self):
        """回归：submit 不得把 username 同时当位置参数和关键字参数。"""
        client = self._client()
        captured = {}
        with patch.object(managers.task_manager, 'submit',
                          side_effect=lambda fn, *a, **kw: captured.update(fn=fn, args=a, kwargs=kw) or 'tid'):
            resp = client.post('/api/shelf/health_check')
        self.assertEqual(resp.get_json()['status'], 'success')
        self.assertNotIn('username', captured['args'])
        self.assertEqual(captured['kwargs'].get('username'), 'reader')
        self._assert_bindable(captured)

    def test_plan_switches_task_receives_username_once(self):
        client = self._client()
        captured = {}
        with patch.object(managers.task_manager, 'submit',
                          side_effect=lambda fn, *a, **kw: captured.update(fn=fn, args=a, kwargs=kw) or 'tid'):
            resp = client.post('/api/shelf/plan_switches', json={'book_keys': ['bk']})
        self.assertEqual(resp.get_json()['status'], 'success')
        self.assertNotIn('username', captured['args'])
        self.assertEqual(captured['kwargs'].get('username'), 'reader')
        self._assert_bindable(captured)
