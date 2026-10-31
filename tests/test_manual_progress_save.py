"""关闭远端进度提示（manual 更新）必须保存当前位置，即使标题解析不出章节号。"""
import json
import unittest
from unittest.mock import Mock, patch

from flask import Flask

import managers
from routes.core_bp import core_bp

CHAPTER_URL = 'https://example.test/book/30.html'


class TestManualProgressSave(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test')
        app.register_blueprint(core_bp)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['user'] = {'username': 'reader'}
        self.db = Mock()
        self.db.update.return_value = {'status': 'success'}
        self.db.get_val.return_value = None

    def post_update(self, **extra):
        payload = {'key': 'book', 'value': CHAPTER_URL, 'title': '番外篇'}
        payload.update(extra)
        return self.client.post('/update', json=payload)

    def test_manual_save_keeps_position_without_chapter_id(self):
        with patch.object(managers, 'db', self.db), \
             patch('routes.core_bp.calculate_real_chapter_id', return_value=-1):
            response = self.post_update(manual=True, resolve=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('跳过', response.json.get('message', ''))
        self.db.update.assert_called_once_with('book', CHAPTER_URL)
        self.db.add_version.assert_called_once_with('book', CHAPTER_URL)

    def test_auto_save_still_skips_unrecognized_chapter(self):
        with patch.object(managers, 'db', self.db), \
             patch('routes.core_bp.calculate_real_chapter_id', return_value=-1):
            response = self.post_update(resolve=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn('跳过', response.json.get('message', ''))
        self.db.update.assert_not_called()

    def test_manual_save_with_chapter_id_updates_meta(self):
        with patch.object(managers, 'db', self.db), \
             patch('routes.core_bp.calculate_real_chapter_id', return_value=12):
            response = self.post_update(manual=True, resolve=False)
        self.assertEqual(response.status_code, 200)
        meta_calls = [call for call in self.db.update.call_args_list if call.args[0] == 'book:meta']
        self.assertEqual(len(meta_calls), 1)
        meta = json.loads(meta_calls[0].args[1])
        self.assertEqual(meta['chapter_id'], 12)


if __name__ == '__main__':
    unittest.main()
