import json
import os
import tempfile
import threading
import unittest
from collections import OrderedDict

import managers


class TestWorkerCache(unittest.TestCase):
    def test_external_worker_replace_add_and_delete(self):
        with tempfile.TemporaryDirectory() as root:
            # Isolate the file-cache reader without initializing production storage.
            cache = managers.FullTextCacheManager.__new__(managers.FullTextCacheManager)
            cache.cache_dir = root
            cache._cache_lock = threading.RLock()
            cache._url_index = {}
            cache._url_index_versions = {}
            cache._book_lru = OrderedDict()
            cache._book_lru_cap = 8
            path = cache._get_cache_path('reader', 'book')

            def publish(url, content):
                with open(path + '.tmp', 'w', encoding='utf-8') as out:
                    json.dump({'chapters': [{'url': url, 'content': content}]}, out)
                os.replace(path + '.tmp', path)

            publish('chapter-1', 'old')
            self.assertEqual(cache.find_book_for_url('chapter-1', 'reader'), 'book')
            self.assertEqual(cache.get_chapter_from_cache('book', 'chapter-1', 'reader')['content'], 'old')
            publish('chapter-2', 'new content')
            self.assertIsNone(cache.find_book_for_url('chapter-1', 'reader'))
            self.assertEqual(cache.find_book_for_url('chapter-2', 'reader'), 'book')
            self.assertEqual(cache.get_chapter_from_cache('book', 'chapter-2', 'reader')['content'], 'new content')
            os.remove(path)
            self.assertIsNone(cache.get_chapter_from_cache('book', 'chapter-2', 'reader'))
            self.assertIsNone(cache.find_book_for_url('chapter-2', 'reader'))
            publish('chapter-3', 'after deletion')
            self.assertEqual(cache.find_book_for_url('chapter-3', 'reader'), 'book')


if __name__ == '__main__':
    unittest.main()
