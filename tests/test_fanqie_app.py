import unittest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from adapters.fanqie_adapter import FanqieLocalAdapter
from adapters.fanqie_app_adapter import FanqieAppAdapter
from recognition import get_payload_issue, is_cacheable_payload

URL = 'https://fanqienovel.com/reader/7210692807986512418?source=seo_fq_juhe'


class FanqieAppTests(unittest.TestCase):
    def setUp(self):
        self.app = FanqieAppAdapter()
        self.app.backend = Mock()

    def test_selection_is_exclusive(self):
        legacy = FanqieLocalAdapter()
        for mode in ('legacy', 'app', 'invalid'):
            with patch.dict('os.environ', {'FANQIE_ADAPTER': mode}):
                self.assertEqual(legacy.can_handle(URL), mode == 'legacy')
                self.assertEqual(self.app.can_handle(URL), mode == 'app')

    def test_failure_and_old_placeholder_are_not_cacheable(self):
        self.app.backend.get_content.return_value = {'code': 1, 'message': 'failed'}
        result = self.app.run(None, URL)
        self.assertEqual(get_payload_issue(result)['code'], 'EMPTY_CHAPTER')
        self.assertFalse(is_cacheable_payload(result))
        self.app.backend.resolve_book_id_by_item.assert_not_called()
        self.assertFalse(is_cacheable_payload({'page_type': 'chapter', 'content': ['❗ 内容获取失败，请稍后重试']}))

    def test_backend_exception_is_not_cacheable(self):
        self.app.backend.get_content.side_effect = TimeoutError()
        self.assertFalse(is_cacheable_payload(self.app.run(None, URL)))

    def test_success_and_navigation(self):
        self.app.backend.get_content.return_value = {'code': 0, 'data': {'content': '一行\n二行'}}
        self.app.backend.resolve_book_id_by_item.return_value = {'book_id': '123'}
        self.app.backend.get_catalog.return_value = {'code': 0, 'data': {'item_data_list': [
            {'item_id': '7210692807986512418', 'title': '第1章 开始'},
            {'item_id': '42', 'title': '第2章 继续'}]}}
        self.app.backend.get_detail.return_value = {'code': 0, 'data': {'book_name': '测试'}}
        result = self.app.run(None, URL)
        self.assertEqual(result['content'], ['一行', '二行'])
        self.assertTrue(result['next'].endswith('/42'))
        self.assertTrue(is_cacheable_payload(result))
        toc = self.app.get_toc(None, 'https://fanqienovel.com/page/123')
        self.assertTrue(toc['manual_sort'])
        self.assertEqual(toc['chapters'][0]['item_id'], '7210692807986512418')

    def test_empty_catalog_not_cached(self):
        self.app.backend.get_catalog.return_value = {'code': 1}
        self.assertEqual(self.app._fetch_toc_list('123'), [])
        self.assertEqual(self.app._toc_cache, {})


if __name__ == '__main__':
    unittest.main()
