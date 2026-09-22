import unittest

from services.searxng_search import SearxngNovelSearch


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

        self.assertEqual(len(requests_seen), 2)
        self.assertIn('"测试作品" 小说 目录', requests_seen)
        self.assertIn('测试作品 小说 在线阅读', requests_seen)
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
