import contextlib
import io
import os
import sqlite3
import socket
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import managers
import shared
from werkzeug.datastructures import FileStorage


class TestSafeUrl(unittest.TestCase):
    def _address(self, value):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (value, 443))]

    @patch.dict(os.environ, {}, clear=False)
    @patch('shared.socket.getaddrinfo')
    def test_accepts_public_address(self, getaddrinfo):
        os.environ.pop('DISABLE_SSRF_CHECK', None)
        getaddrinfo.return_value = self._address('93.184.216.34')
        self.assertTrue(shared.is_safe_url('https://example.com/book/1'))

    @patch.dict(os.environ, {}, clear=False)
    @patch('shared.socket.getaddrinfo')
    def test_rejects_private_and_mixed_dns_answers(self, getaddrinfo):
        os.environ.pop('DISABLE_SSRF_CHECK', None)
        getaddrinfo.return_value = (
            self._address('93.184.216.34') + self._address('127.0.0.1')
        )
        self.assertFalse(shared.is_safe_url('https://example.com/book/1'))

    def test_rejects_localhost_and_url_credentials(self):
        self.assertFalse(shared.is_safe_url('http://localhost/admin'))
        self.assertFalse(shared.is_safe_url('https://user:pass@example.com/'))

    @patch.dict(
        os.environ,
        {'DISABLE_SSRF_CHECK': '1', 'APP_ENV': 'production'},
        clear=False,
    )
    @patch('shared.socket.getaddrinfo')
    def test_production_cannot_disable_ssrf_check(self, getaddrinfo):
        getaddrinfo.return_value = self._address('10.0.0.1')
        self.assertFalse(shared.is_safe_url('http://internal.example/'))


class TestMemoIsolation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, 'test.sqlite')

        @contextlib.contextmanager
        def temporary_db():
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
            finally:
                connection.close()

        self.db_patcher = patch('managers.get_db', temporary_db)
        self.db_patcher.start()
        self.manager = managers.MemoManager()

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def test_memo_operations_are_scoped_to_owner(self):
        memo_id = self.manager.save_memo('alice', title='private')

        self.assertIsNotNone(self.manager.get_memo('alice', memo_id))
        self.assertIsNone(self.manager.get_memo('bob', memo_id))
        self.assertIsNone(
            self.manager.save_memo('bob', memo_id=memo_id, title='stolen')
        )
        self.assertFalse(self.manager.toggle_pin('bob', memo_id))
        self.assertFalse(self.manager.delete_memo('bob', memo_id))
        self.assertEqual(
            self.manager.get_memo('alice', memo_id)['title'],
            'private',
        )


class TestOAuthState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from flask import Flask
        from routes.core_bp import core_bp

        cls.app = Flask(__name__)
        cls.app.config.update(TESTING=True, SECRET_KEY='test-secret')
        cls.app.register_blueprint(core_bp)

    def setUp(self):
        self.client = self.app.test_client()

    @patch('routes.core_bp.CLIENT_ID', 'client-id')
    @patch('routes.core_bp.REDIRECT_URI', 'https://app.example/callback')
    @patch('routes.core_bp.AUTH_SERVER', 'https://auth.example')
    def test_login_stores_and_sends_state(self):
        response = self.client.get('/login')
        query = parse_qs(urlparse(response.location).query)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(query['client_id'], ['client-id'])
        self.assertEqual(query['redirect_uri'], ['https://app.example/callback'])
        with self.client.session_transaction() as login_session:
            self.assertEqual(query['state'], [login_session['oauth_state']])

    @patch('routes.core_bp.requests.post')
    def test_callback_rejects_mismatched_state_before_token_exchange(self, post):
        with self.client.session_transaction() as login_session:
            login_session['oauth_state'] = 'expected'

        response = self.client.get('/callback?code=code&state=unexpected')

        self.assertEqual(response.status_code, 400)
        post.assert_not_called()


class TestQuickSave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from flask import Flask
        from routes.core_bp import core_bp

        cls.app = Flask(__name__)
        cls.app.config.update(TESTING=True, SECRET_KEY='test-secret')
        cls.app.register_blueprint(core_bp)

    def setUp(self):
        self.client = self.app.test_client()
        with self.client.session_transaction() as login_session:
            login_session['user'] = {'username': 'alice'}

    @patch('routes.core_bp.managers.db.insert')
    @patch('routes.core_bp.managers.db.get_raw_book')
    def test_existing_book_is_not_overwritten_by_quick_save(self, get_raw_book, insert):
        get_raw_book.return_value = {
            'value': {'url': 'https://example.test/toc', 'marked_chapters': [{}]}
        }

        response = self.client.post(
            '/api/quick_save',
            json={'key': 'example-book', 'url': 'https://example.test/new-toc'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'success')
        self.assertTrue(response.get_json()['already_saved'])
        insert.assert_not_called()

    @patch('routes.core_bp.managers.db.insert')
    @patch('routes.core_bp.managers.db.get_raw_book', return_value=None)
    def test_new_book_is_saved_by_quick_save(self, get_raw_book, insert):
        insert.return_value = {'status': 'success'}

        response = self.client.post(
            '/api/quick_save',
            json={'key': 'new-book', 'url': 'https://example.test/toc'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'success')
        self.assertFalse(response.get_json()['already_saved'])
        insert.assert_called_once_with('new-book', 'https://example.test/toc')


class TestForceRefreshBook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from flask import Flask
        from routes.core_bp import core_bp

        cls.app = Flask(__name__)
        cls.app.config.update(TESTING=True, SECRET_KEY='test-secret')
        cls.app.register_blueprint(core_bp)

    def setUp(self):
        self.client = self.app.test_client()
        with self.client.session_transaction() as login_session:
            login_session['user'] = {'username': 'alice'}

    @patch('routes.core_bp.managers.fulltext_cache_manager.delete_cache')
    @patch('routes.core_bp.managers.cache.delete')
    @patch('routes.core_bp.managers.db.save_raw_book')
    @patch('routes.core_bp.managers.db.get_raw_book')
    def test_force_refresh_preserves_reader_data_only(self, get_book, save_book, delete_cache, delete_fulltext):
        get_book.return_value = {
            'key': 'book',
            'value': {
                'url': 'https://example.test/toc',
                'title': '旧书名',
                'author': '旧作者',
                'last_read_url': 'https://example.test/3.html',
                'last_read_index': 3,
                'marked_chapters': [{'url': 'https://example.test/2.html'}],
                'memos': [{'text': '保留'}],
            },
            'tags': ['正在读'],
            'meta': {'cover': 'https://example.test/cover.jpg'},
            'cache': {'toc': {'chapters': [{'url': 'https://example.test/1.html'}]}},
            'update_info': {'latest': '旧章节'},
        }
        save_book.return_value = True
        delete_fulltext.return_value = {'status': 'success'}

        response = self.client.post('/api/book/force_refresh', json={'key': 'book'})

        self.assertEqual(response.status_code, 200)
        saved = save_book.call_args.args[2]
        self.assertEqual(saved['value']['url'], 'https://example.test/toc')
        self.assertEqual(saved['value']['last_read_index'], 3)
        self.assertEqual(saved['value']['marked_chapters'], [{'url': 'https://example.test/2.html'}])
        self.assertEqual(saved['tags'], ['正在读'])
        self.assertEqual(saved['meta'], {})
        self.assertEqual(saved['cache'], {})
        self.assertEqual(saved['update_info'], {})
        self.assertNotIn('title', saved['value'])
        self.assertGreaterEqual(delete_cache.call_count, 3)
        delete_fulltext.assert_called_once_with('book', 'alice')


class TestEpubUploadValidation(unittest.TestCase):
    def setUp(self):
        from spider_core import EpubHandler

        self.temp_dir = tempfile.TemporaryDirectory()
        self.handler = EpubHandler()
        self.handler.lib_dir = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def _file_storage(self, payload, filename='book.epub'):
        return FileStorage(stream=io.BytesIO(payload), filename=filename)

    def test_rejects_non_zip_epub(self):
        with self.assertRaisesRegex(ValueError, '有效的 EPUB'):
            self.handler.save_file(self._file_storage(b'not a zip archive'))

    def test_accepts_valid_epub_container_with_randomized_filename(self):
        archive_buffer = io.BytesIO()
        import zipfile
        with zipfile.ZipFile(archive_buffer, 'w') as archive:
            archive.writestr('mimetype', 'application/epub+zip')
            archive.writestr('META-INF/container.xml', '<container/>')

        filename = self.handler.save_file(self._file_storage(archive_buffer.getvalue()))

        self.assertTrue(filename.endswith('_book.epub'))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir.name, filename)))


class TestCsrfGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import dbserver

        cls.server = dbserver
        cls.app = dbserver.app

    def _request_context(self, headers=None):
        return self.app.test_request_context(
            '/api/memos/save',
            method='POST',
            base_url='http://localhost',
            headers=headers or {},
        )

    def test_rejects_session_write_without_token(self):
        with self._request_context({'Origin': 'http://localhost'}):
            from flask import session

            session['user'] = {'username': 'alice'}
            session['csrf_token'] = 'expected-token'
            response = self.server.basic_csrf_guard()

        self.assertEqual(response[1], 403)

    def test_allows_same_origin_session_write_with_matching_token(self):
        with self._request_context({
            'Origin': 'http://localhost',
            'X-CSRF-Token': 'expected-token',
        }):
            from flask import session

            session['user'] = {'username': 'alice'}
            session['csrf_token'] = 'expected-token'
            self.assertIsNone(self.server.basic_csrf_guard())

    def test_sets_csrf_cookie_for_authenticated_session(self):
        with self.app.test_request_context('/', base_url='http://localhost'):
            from flask import session

            session['user'] = {'username': 'alice'}
            response = self.app.process_response(self.app.make_response('ok'))

        self.assertIn('notedb_csrf=', response.headers['Set-Cookie'])


if __name__ == '__main__':
    unittest.main()
