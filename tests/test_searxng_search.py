import unittest
from unittest import mock

import requests

from services.searxng_search import SearxngNovelSearch, SearxngSearchError


class TestSearxngNovelSearch(unittest.TestCase):
    def setUp(self):
        self.service = SearxngNovelSearch('http://searx.test', cache_ttl=600)

    def test_filters_noise_deduplicates_and_ranks_novel_results(self):
        raw_results = [
            (0, {
                'url': 'https://example.org/book/123?utm_source=bing',
                'title': '凡人修仙传 小说目录',
                'content': '忘语作品，最新章节在线阅读',
                'engines': ['bing'],
                'score': 0.9,
            }),
            (1, {
                'url': 'https://example.org/book/123',
                'title': '凡人修仙传 - 全文阅读',
                'content': '同一来源的重复结果',
                'engine': 'google',
            }),
            (0, {
                'url': 'https://baike.baidu.com/item/test',
                'title': '凡人修仙传 百度百科',
                'content': '百科资料',
            }),
            (0, {
                'url': 'https://download.example.org/novel',
                'title': '凡人修仙传 TXT下载',
                'content': '下载地址',
            }),
            (1, {
                'url': 'https://reader.example.org/novel/456',
                'title': '凡人修仙传同人',
                'content': '相关作品',
            }),
        ]

        results = self.service._normalize_and_rank('凡人修仙传', raw_results)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['url'], 'https://example.org/book/123')
        self.assertEqual(results[0]['source'], 'SearXNG / bing')
        self.assertEqual(results[0]['status'], 'unverified')
        self.assertGreater(results[0]['match_score'], results[1]['match_score'])

    def test_search_builds_novel_queries_and_uses_ttl_cache(self):
        requests_seen = []

        def fake_request(query):
            requests_seen.append(query)
            return [{
                'url': 'https://reader.example.org/book/1',
                'title': '测试作品 小说目录',
                'content': '测试作品在线阅读',
                'engine': 'bing',
            }]

        self.service._request = fake_request
        first = self.service.search('测试作品')
        second = self.service.search('测试作品')

        self.assertEqual(len(requests_seen), 3)
        self.assertIn('测试作品 小说', requests_seen)
        self.assertIn('测试作品 小说 在线阅读', requests_seen)
        self.assertIn('测试作品', requests_seen)
        self.assertEqual(first, second)

    def test_filters_site_search_and_redirect_urls_before_verification(self):
        raw_results = [
            (0, {
                'url': 'https://www.hongxiu.com/bookquery/%E5%87%A1%E4%BA%BA%E4%BF%AE%E4%BB%99%E4%BC%A0',
                'title': '凡人修仙传 搜索结果', 'content': '站内搜索',
            }),
            (0, {
                'url': 'https://www.xxsy.net/search?keyword=%E5%87%A1%E4%BA%BA',
                'title': '凡人修仙传 搜索', 'content': '站内搜索',
            }),
            (0, {
                'url': 'https://www.sogou.com/link?url=example',
                'title': '凡人修仙传', 'content': '跳转页',
            }),
            (0, {
                'url': 'https://reader.example.org/book/123',
                'title': '凡人修仙传 小说目录', 'content': '忘语作品',
            }),
        ]

        results = self.service._normalize_and_rank('凡人修仙传', raw_results)

        self.assertEqual([item['url'] for item in results], ['https://reader.example.org/book/123'])

    def test_verification_promotes_readable_candidates_and_preserves_unchecked_results(self):
        self.service.verify_limit = 2
        self.service.verify_workers = 1

        def fake_request(_query):
            return [
                {'url': 'https://first.example.org/book/1', 'title': '测试作品 小说目录', 'content': '目录', 'engine': 'bing'},
                {'url': 'https://second.example.org/book/2', 'title': '测试作品 在线阅读', 'content': '正文', 'engine': 'bing'},
                {'url': 'https://third.example.org/book/3', 'title': '测试作品 章节列表', 'content': '章节', 'engine': 'bing'},
            ]

        def verify(item):
            if 'second.' in item['url']:
                return {'status': 'ready_catalog', 'status_detail': '已识别目录，12 章'}
            return {'status': 'unavailable', 'status_detail': '无法读取'}

        self.service._request = fake_request
        results = self.service.search('测试作品', verifier=verify)

        self.assertEqual(results[0]['url'], 'https://second.example.org/book/2')
        self.assertEqual(results[0]['status'], 'ready_catalog')
        self.assertIn('unverified', [item['status'] for item in results])
        self.assertEqual(results[-1]['status'], 'unavailable')

    def test_verifier_receives_original_keyword_without_exposing_it_in_results(self):
        self.service.verify_limit = 1
        self.service._request = lambda _query: [{
            'url': 'https://reader.example.org/book/1',
            'title': '测试作品 小说目录', 'content': '目录', 'engine': 'bing',
        }]
        received = []

        def verify(item):
            received.append(item['_search_keyword'])
            return {'status': 'ready_catalog', 'status_detail': '已确认'}

        results = self.service.search('测试作品', verifier=verify)

        self.assertEqual(received, ['测试作品'])
        self.assertNotIn('_search_keyword', results[0])

    def test_all_request_failures_raise_and_are_not_cached_as_empty_results(self):
        requests_seen = []

        def failing_request(query):
            requests_seen.append(query)
            raise requests.ConnectionError('searx offline')

        self.service._request = failing_request
        messages = []

        with self.assertRaises(SearxngSearchError) as raised:
            self.service.search(
                '测试作品',
                callback=lambda _progress, message, _results: messages.append(message),
            )

        self.assertEqual(len(requests_seen), 3)
        self.assertEqual(len(raised.exception.failures), 3)
        self.assertIn('ConnectionError', str(raised.exception))
        self.assertIn('searx.test', str(raised.exception))
        self.assertFalse(any('聚合完成' in message for message in messages))
        self.assertNotIn('测试作品', self.service._cache)

        def recovered_request(query):
            requests_seen.append(query)
            return [{
                'url': 'https://reader.example.org/book/1',
                'title': '测试作品 小说目录',
                'content': '测试作品在线阅读',
                'engine': 'bing',
            }]

        self.service._request = recovered_request
        results = self.service.search('测试作品')

        self.assertTrue(results)
        self.assertEqual(len(requests_seen), 6)

    def test_successful_empty_search_is_cached(self):
        requests_seen = []
        self.service._request = lambda query: requests_seen.append(query) or []

        self.assertEqual(self.service.search('测试作品'), [])
        self.assertEqual(self.service.search('测试作品'), [])

        self.assertEqual(len(requests_seen), 3)

    def test_bypass_cache_forces_fresh_results_and_refreshes_cache(self):
        requests_seen = []

        def fake_request(query):
            requests_seen.append(query)
            generation = 'cached' if len(requests_seen) <= 2 else 'fresh'
            return [{
                'url': f'https://{generation}.example.org/book/1',
                'title': f'测试作品 {generation} 小说目录',
                'content': '测试作品在线阅读',
                'engine': 'bing',
            }]

        self.service._request = fake_request
        first = self.service.search('测试作品')
        second = self.service.search('测试作品')
        forced = self.service.search('测试作品', bypass_cache=True)
        refreshed = self.service.search('测试作品')

        self.assertEqual(len(requests_seen), 6)
        self.assertEqual(second, first)
        self.assertEqual(forced[0]['url'], 'https://fresh.example.org/book/1')
        self.assertEqual(refreshed, forced)

    def test_verifier_runs_for_unverified_cached_results(self):
        self.service.verify_limit = 2
        self.service.verify_workers = 1
        self.service._request = lambda _query: [
            {'url': 'https://first.example.org/book/1', 'title': '测试作品 小说目录', 'content': '目录', 'engine': 'bing'},
            {'url': 'https://second.example.org/book/2', 'title': '测试作品 在线阅读', 'content': '正文', 'engine': 'bing'},
        ]
        verified_urls = []

        def verify(item):
            verified_urls.append(item['url'])
            return {'status': 'ready_catalog', 'status_detail': '已确认'}

        first = self.service.search('测试作品')
        self.assertEqual(verified_urls, [])
        self.assertTrue(all(item['status'] == 'unverified' for item in first))

        second = self.service.search('测试作品', verifier=verify)
        self.assertEqual(len(verified_urls), 2)
        self.assertTrue(all(item['status'] == 'ready_catalog' for item in second))

        third = self.service.search('测试作品', verifier=verify)
        self.assertEqual(len(verified_urls), 2)
        self.assertEqual(third, second)


class TestSearchHelperEntryPoints(unittest.TestCase):
    def setUp(self):
        from spider_core import SearchHelper

        self.helper = SearchHelper()
        self.helper.searxng = mock.Mock()
        self.helper.searxng.search.return_value = []
        self.helper._verify_candidate = mock.Mock(
            return_value={'status': 'unverified', 'status_detail': '未验证'},
        )

    def test_search_bing_bypasses_the_service_cache(self):
        self.helper.search_bing('测试作品')

        self.helper.searxng.search.assert_called_once_with(
            '测试作品',
            verifier=self.helper._verify_candidate,
            bypass_cache=True,
        )

    def test_cached_and_concurrent_entries_keep_using_the_service_cache(self):
        callback = mock.Mock()
        self.helper.search_bing_cached('测试作品')
        self.helper.search_concurrent('测试作品', callback)

        cached_call, concurrent_call = self.helper.searxng.search.call_args_list
        self.assertEqual(cached_call.args, ('测试作品',))
        self.assertIs(cached_call.kwargs['verifier'], self.helper._verify_candidate)
        self.assertFalse(cached_call.kwargs.get('bypass_cache', False))
        self.assertEqual(concurrent_call.args, ('测试作品', callback))
        self.assertIs(concurrent_call.kwargs['verifier'], self.helper._verify_candidate)
        self.assertFalse(concurrent_call.kwargs.get('bypass_cache', False))

    def test_search_bing_cached_has_no_process_level_lru_cache(self):
        self.helper.search_bing_cached('测试作品')
        self.helper.search_bing_cached('测试作品')

        self.assertEqual(self.helper.searxng.search.call_count, 2)
