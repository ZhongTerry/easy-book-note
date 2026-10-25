import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import Mock, patch

import managers
import spider_core


class TestAtomicBookUpdates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, 'books.sqlite')
        self.db_patch = patch.object(managers, 'DB_PATH', self.path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.db = managers.IsolatedDB()
        self.db.save_raw_book('reader', 'book', {
            'value': {'url': 'chapter-10'}, 'tags': ['keep'],
            'update_info': {'toc_url': 'toc-a', 'last_local_id': 10},
        })

    def check(self, toc='toc-a', remote=20):
        with closing(sqlite3.connect(self.path)) as conn:
            return managers.save_remote_check(conn, 'reader', 'book', toc, remote)

    def test_delayed_check_preserves_new_progress_source_and_tags(self):
        self.db.update('book', {'url': 'new-source/chapter-30'}, username='reader')
        current = self.db.get_raw_book('reader', 'book')
        current['update_info']['last_local_id'] = 30
        current['tags'] = ['new-tag']
        self.db.save_raw_book('reader', 'book', current)
        self.assertTrue(self.check())
        saved = self.db.get_raw_book('reader', 'book')
        self.assertEqual(saved['value']['url'], 'new-source/chapter-30')
        self.assertEqual(saved['tags'], ['new-tag'])
        self.assertFalse(saved['update_info']['has_update'])

    def test_changed_subscription_or_deleted_book_discards_delayed_result(self):
        current = self.db.get_raw_book('reader', 'book')
        current['update_info']['toc_url'] = 'toc-b'
        self.db.save_raw_book('reader', 'book', current)
        self.assertFalse(self.check())
        self.assertNotIn('last_remote_id', self.db.get_raw_book('reader', 'book')['update_info'])
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('DELETE FROM books_v2')
            conn.commit()
        self.assertFalse(self.check())
        self.assertIsNone(self.db.get_raw_book('reader', 'book'))

    def test_parallel_updates_merge_without_losing_other_fields(self):
        barrier = threading.Barrier(2)
        def progress():
            barrier.wait(timeout=5)
            return self.db.update('book', {'url': 'chapter-11'}, username='reader')
        def toc():
            barrier.wait(timeout=5)
            return self.db.save_toc_cache('book', {'chapters': [], 'author': 'author'}, username='reader')
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(progress), pool.submit(toc)
            self.assertEqual(a.result()['status'], 'success')
            self.assertTrue(b.result())
        saved = self.db.get_raw_book('reader', 'book')
        self.assertEqual(saved['value']['url'], 'chapter-11')
        self.assertEqual(saved['meta']['author'], 'author')
        self.assertEqual(saved['tags'], ['keep'])


class TestTocRequestIsolation(unittest.TestCase):
    def setUp(self):
        self.crawler = spider_core.NovelCrawler.__new__(spider_core.NovelCrawler)
        self.crawler.timeout = 17
        self.crawler.impersonate = 'chrome110'
        self.crawler.proxies = None

    def test_fast_and_normal_requests_do_not_share_timeouts(self):
        barrier = threading.Barrier(2)
        def fetch(url, **kwargs):
            return kwargs['timeout']
        def get(url, **kwargs):
            barrier.wait(timeout=5)
            return self.crawler._fetch_page_smart(url)
        with patch.object(self.crawler, '_get_toc', side_effect=get), \
             patch.object(self.crawler, '_decode_page_response', side_effect=lambda r: r), \
             patch.object(spider_core.cffi_requests, 'get', side_effect=fetch):
            with ThreadPoolExecutor(max_workers=2) as pool:
                fast = pool.submit(self.crawler.get_toc, 'fast', fast_mode=True)
                normal = pool.submit(self.crawler.get_toc, 'normal')
                self.assertEqual(fast.result(), 5)
                self.assertEqual(normal.result(), 15)
        self.assertEqual(self.crawler.timeout, 17)
        self.assertIsNone(spider_core._toc_request_options.get())

    def test_exception_restores_request_context(self):
        with patch.object(self.crawler, '_get_toc', side_effect=ValueError('test')):
            with self.assertRaises(ValueError):
                self.crawler.get_toc('test', fast_mode=True)
        self.assertIsNone(spider_core._toc_request_options.get())

    def test_adapter_called_once_and_manual_order_keeps_metadata(self):
        adapter = Mock()
        adapter.get_toc.return_value = {
            'manual_sort': True, 'chapters': [{'url': 'chapter'}], 'tags': ['original'],
        }
        adapter.get_meta.return_value = {'cover': 'cover', 'author': 'author'}
        with patch.object(spider_core, '_remote_request', return_value=None), \
             patch.object(spider_core.plugin_mgr, 'find_match', return_value=adapter), \
             patch.object(self.crawler, '_recognize_toc_payload', side_effect=lambda data, url: data):
            result = self.crawler.get_toc('https://example.test/book', no_cache=True)
        adapter.get_toc.assert_called_once()
        adapter.get_meta.assert_called_once()
        self.assertEqual(result['cover'], 'cover')
        self.assertEqual(result['tags'], ['original'])


class TestSourceSearchFailure(unittest.TestCase):
    def test_source_list_returns_diagnostic_json_instead_of_html_500(self):
        from flask import Flask
        from routes.core_bp import core_bp
        from services.searxng_search import SearxngSearchError
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test')
        app.register_blueprint(core_bp)
        client = app.test_client()
        with client.session_transaction() as session:
            session['user'] = {'username': 'reader'}
        exc = SearxngSearchError('http://searx.test', '测试作品', [
            (0, '测试作品', 'Timeout', 'simulated'),
        ])
        with patch('routes.core_bp.is_safe_url', return_value=True), \
             patch('routes.core_bp.managers.booklist_manager.load', return_value={}), \
             patch('spider_core.searcher.search_bing_cached', side_effect=exc):
            response = client.post('/api/source/list', json={
                'url': 'https://example.test/1', 'key': 'somebook',
                'manual_book_name': '测试作品', 'title': '第12章 测试作品',
            })
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json['code'], 'SEARXNG_UNAVAILABLE')
        self.assertIn('Timeout', response.json['msg'])


if __name__ == '__main__':
    unittest.main()
