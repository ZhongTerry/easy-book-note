"""换源服务测试：书名解析、验证前置、应用换源与缓存清理。"""
import json
import os
import unittest
from unittest.mock import Mock, patch

import managers
from services import source_switch


class SourceSwitchBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db_patch = patch.object(managers, 'DB_PATH',
                                os.path.join(self.temp.name, 't.sqlite'))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        managers.IsolatedDB()
        # 本机 DNS 对虚构域名不可用；模块内所有换源地址测试一律视为安全
        ss_patch = patch('shared.is_safe_url', return_value=True)
        ss_patch.start()
        self.addCleanup(ss_patch.stop)


class TestResolveBookName(SourceSwitchBase):
    def _seed_book(self, key='bk', title='凡人修仙传'):
        managers.db.save_raw_book('reader', key, {
            'value': {'url': 'http://s/1.html', 'title': title},
            'meta': {'title': title},
        })

    def test_book_record_title_wins_over_pinyin(self):
        self._seed_book(title='凡人修仙传')
        name, source = source_switch.resolve_book_name(
            'bk', 'http://s/1.html', manual_book_name='', crawler=None)
        self.assertEqual((name, source), ('凡人修仙传', 'book_record'))

    def test_manual_input_has_top_priority(self):
        self._seed_book(title='凡人修仙传')
        name, source = source_switch.resolve_book_name(
            'bk', 'http://s/1.html', manual_book_name=' fabricating 手动名 ',
            crawler=None)
        self.assertEqual((name, source), ('fabricating 手动名', 'manual'))

    def test_pinyin_key_falls_through_to_booklist(self):
        managers.db.save_raw_book('reader', 'frxxz', {'value': {'url': 'http://s/1.html'}})
        managers.booklist_manager.add_list('mylist', username='reader')
        lid = list(managers.booklist_manager.load('reader').keys())[0]
        data = managers.booklist_manager.load('reader')
        data[lid]['books'].append({'key': 'frxxz', 'title': '凡人修仙传'})
        managers.booklist_manager.save(data, 'reader')

        name, source = source_switch.resolve_book_name(
            'frxxz', 'http://s/1.html', crawler=None, username='reader')
        self.assertEqual((name, source), ('凡人修仙传', 'booklist'))

    def test_crawl_fallback_uses_book_name_field(self):
        crawler = Mock()
        crawler.run.return_value = {'book_name': '诡秘之主', 'title': '第1章'}
        name, source = source_switch.resolve_book_name(
            'guiMi', 'http://s/1.html', crawler=crawler)
        self.assertEqual((name, source), ('诡秘之主', 'crawled'))
        crawler.run.assert_called_once_with('http://s/1.html')

    def test_ascii_only_title_is_rejected(self):
        managers.db.save_raw_book('reader', 'bk', {'value': {'title': 'wbsxs'}})
        name, source = source_switch.resolve_book_name('bk', 'http://s/1.html',
                                                       crawler=None)
        self.assertIsNone(name)


class TestFindSources(SourceSwitchBase):
    def _crawler(self, toc_chapters):
        crawler = Mock()
        crawler.get_toc.return_value = {'chapters': toc_chapters}
        return crawler

    def _searcher(self, results):
        searcher = Mock()
        searcher.search_bing_cached.return_value = results
        return searcher

    def test_only_verified_sources_with_matching_chapter(self):
        chapters = [
            {'id': 1, 'name': '第1章', 'url': 'http://new.test/1/1.html'},
            {'id': 8, 'name': '第8章 风起', 'url': 'http://new.test/1/8.html'},
        ]
        crawler = self._crawler(chapters)
        searcher = self._searcher([
            {'url': 'http://new.test/1/'},
            {'url': 'http://other.test/1/'},
        ])
        managers.db.save_raw_book('reader', 'bk', {'value': {'title': '测试书'}})

        with patch('spider_core.searcher', searcher), \
                patch('shared.is_safe_url', return_value=True):
            result = source_switch.find_sources(
                crawler, 'http://old.test/1/7.html', 'bk', '第8章 风起',
                username='reader')

        self.assertEqual(result['book_name'], '测试书')
        self.assertEqual(result['chapter_id'], 8)
        self.assertEqual(len(result['sources']), 2)  # 两个候选目录都含第8章
        self.assertEqual({s['source'] for s in result['sources']},
                         {'new.test', 'other.test'})
        for s in result['sources']:
            self.assertTrue(s['url'].endswith('/1/8.html'))
            self.assertGreater(s['confidence'], 0)

    def test_current_source_domain_excluded(self):
        chapters = [{'id': 8, 'name': '第8章', 'url': 'http://old.test/1/8.html'}]
        crawler = self._crawler(chapters)
        searcher = self._searcher([{'url': 'http://old.test/1/'}])  # 搜索还回了当前源
        managers.db.save_raw_book('reader', 'bk', {'value': {'title': '测试书'}})

        with patch('spider_core.searcher', searcher), \
                patch('shared.is_safe_url', return_value=True):
            result = source_switch.find_sources(
                crawler, 'http://old.test/1/7.html', 'bk', '第8章',
                username='reader')

        self.assertEqual(result['sources'], [])

    def test_unverifiable_candidates_yield_empty(self):
        crawler = self._crawler([{'id': 99, 'name': '别的书章节', 'url': 'http://new.test/99.html'}])
        searcher = self._searcher([{'url': 'http://new.test/'}])
        managers.db.save_raw_book('reader', 'bk', {'value': {'title': '测试书'}})

        with patch('spider_core.searcher', searcher),                 patch('shared.is_safe_url', return_value=True):
            result = source_switch.find_sources(
                crawler, 'http://old.test/1/7.html', 'bk', '第8章',
                username='reader')

        self.assertEqual(result['sources'], [])

    def test_missing_chapter_number_raises_clear_error(self):
        crawler = Mock()
        searcher = Mock()
        managers.db.save_raw_book('reader', 'bk', {'value': {'title': '测试书'}})
        with patch('spider_core.searcher', searcher), \
                patch('shared.is_safe_url', return_value=True):
            with self.assertRaises(LookupError) as ctx:
                source_switch.find_sources(crawler, 'http://s/1.html', 'bk',
                                           '番外·雪夜', username='reader')
        self.assertIn('章节号', str(ctx.exception))


class TestApplySwitch(SourceSwitchBase):
    def _crawler(self, payload):
        crawler = Mock()
        crawler.run.return_value = payload
        return crawler

    def test_apply_updates_url_and_clears_old_cache(self):
        managers.db.save_raw_book('reader', 'bk', {'value': {'url': 'http://old.test/1/7.html'}})
        managers.cache.set('http://old.test/1/7.html', {'stale': True})
        crawler = self._crawler({
            'page_type': 'chapter', 'title': '第8章 风起',
            'content': ['段落'], 'prev': None, 'next': None,
        })

        result = source_switch.apply_switch(
            'reader', 'bk', 'http://new.test/1/8.html', crawler, expected_id=8)

        self.assertEqual(result['status'], 'applied')
        self.assertIsNone(managers.cache.get('http://old.test/1/7.html'))
        self.assertEqual(managers.db.get_full_data('bk', 'reader')['url'],
                         'http://new.test/1/8.html')

    def test_apply_rejects_toc_payload(self):
        managers.db.save_raw_book('reader', 'bk', {'value': {'url': 'http://old.test/1/7.html'}})
        crawler = self._crawler({'page_type': 'toc', 'chapters': [{'url': 'x'}],
                                 'content': []})
        result = source_switch.apply_switch(
            'reader', 'bk', 'http://new.test/1/', crawler)
        self.assertEqual(result['status'], 'error')
        self.assertIn('未做任何修改', result['message'])
        self.assertEqual(managers.db.get_full_data('bk', 'reader')['url'],
                         'http://old.test/1/7.html')

    def test_apply_notes_id_mismatch(self):
        managers.db.save_raw_book('reader', 'bk', {'value': {'url': 'http://old.test/1/7.html'}})
        crawler = self._crawler({'page_type': 'chapter', 'title': '第9章 错位',
                                 'content': ['段落']})
        result = source_switch.apply_switch(
            'reader', 'bk', 'http://new.test/1/9.html', crawler, expected_id=8)
        self.assertEqual(result['status'], 'applied')
        self.assertIn('9', result['note'])
        self.assertIn('8', result['note'])


if __name__ == '__main__':
    unittest.main()
