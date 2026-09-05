import json
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import Flask, render_template

from recognition import PageType, RecognitionEngine, SourceHealthTracker, find_chapter_match, get_payload_issue, is_cacheable_payload
from recognition.chapter_numbers import normalize_chapter_title, parse_chapter_number


FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'recognition'


class TestRecognitionFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = RecognitionEngine()
        cls.manifest = json.loads((FIXTURE_DIR / 'manifest.json').read_text(encoding='utf-8'))

    def analyze(self, name):
        html = (FIXTURE_DIR / f'{name}.html').read_text(encoding='utf-8')
        return self.engine.analyze_html(html, 'https://novel.example/book/')

    def test_fixture_manifest_has_complete_page_type_coverage(self):
        fixture_types = {item['page_type'] for item in self.manifest.values()}
        self.assertTrue({'chapter', 'toc', 'blocked'}.issubset(fixture_types))

    def test_standard_chapter(self):
        expected = self.manifest['chapter_standard']
        result = self.analyze('chapter_standard')

        self.assertEqual(result.page_type.value, expected['page_type'])
        self.assertEqual(result.title, expected['title'])
        self.assertIn(expected['content_contains'], result.content)
        self.assertEqual(result.next_url, expected['next_url'])
        self.assertGreaterEqual(result.confidence, 0.55)

    def test_standard_toc(self):
        expected = self.manifest['toc_standard']
        result = self.analyze('toc_standard')

        self.assertEqual(result.page_type.value, expected['page_type'])
        self.assertEqual(len(result.chapters), expected['chapter_count'])
        self.assertEqual(result.chapters[0]['title'], expected['first_chapter'])
        self.assertGreaterEqual(result.confidence, 0.65)

    def test_volume_headings_are_attached_to_chapters(self):
        result = self.analyze('toc_volumes')

        self.assertEqual(result.page_type, PageType.TOC)
        self.assertEqual(result.chapters[0]['volume'], '第一卷 初入江湖')
        self.assertEqual(result.chapters[-1]['volume'], '第二卷 风云再起')

    def test_blocked_page(self):
        result = self.analyze('blocked_challenge')

        self.assertEqual(result.page_type, PageType.BLOCKED)
        self.assertIn('source_blocked_or_challenged', result.warnings)

    def test_cloudflare_browser_challenge_is_not_mistaken_for_content(self):
        result = self.analyze('cloudflare_challenge')

        self.assertEqual(result.page_type, PageType.BLOCKED)
        self.assertFalse(result.content)
        self.assertEqual(result.evidence[0].code, 'blocked_marker')

    def test_noisy_chapter_filters_known_noise(self):
        expected = self.manifest['noisy_chapter']
        result = self.analyze('noisy_chapter')

        self.assertEqual(result.page_type.value, expected['page_type'])
        self.assertIn(expected['content_contains'], result.content)
        self.assertNotIn('一秒记住本站最新地址。', result.content)

    def test_content_cleaner_removes_structured_ads_and_long_boilerplate(self):
        result = self.analyze('boilerplate_chapter')

        self.assertEqual(result.page_type, PageType.CHAPTER)
        self.assertEqual(len(result.content), 3)
        self.assertTrue(all('收藏本站' not in line for line in result.content))
        self.assertTrue(any(item.code == 'content_noise_removed' for item in result.evidence))

    def test_paginated_chapter_first_page(self):
        expected = self.manifest['paginated_chapter']
        result = self.analyze('paginated_chapter_1')

        self.assertEqual(result.page_type.value, expected['page_type'])
        self.assertIn('第一页的正文从清晨开始。', result.content)
        self.assertEqual(result.next_page_url, 'https://novel.example/book/10_2.html')

    def test_numeric_chapter_suffix_is_recognized_as_next_page(self):
        result = self.engine.analyze_html(
            '''<html><head><title>第2657章 测试（1 / 2）</title></head>
            <body><h1>第2657章 测试（1 / 2）</h1>
            <article>这一页有足够长度的正文，用于验证真实书源常见的分段章节导航不会被误判为下一章。</article>
            <a href="/xs/23389/34786180.html">上一章</a>
            <a href="/xs/23389/34786186_2.html">下一页</a>
            <a href="/xs/23389/">书页/目录</a></body></html>''',
            'https://www.luyouxs.com/xs/23389/34786186.html',
        )

        self.assertEqual(
            result.next_page_url,
            'https://www.luyouxs.com/xs/23389/34786186_2.html',
        )
        self.assertIsNone(result.next_url)

    def test_paginated_titles_normalize_to_the_same_chapter(self):
        self.assertEqual(
            normalize_chapter_title('第3169章 示例（1 / 2）'),
            normalize_chapter_title('第3169章 示例（2 / 2）'),
        )


class TestRecognitionNormalization(unittest.TestCase):
    def setUp(self):
        self.engine = RecognitionEngine()

    def test_chapter_number_formats(self):
        from spider_core import parse_chapter_id

        cases = {
            '第12章 风起': 12,
            '第十二章 风起': 12,
            '第二百零三回': 203,
            'chapter 42': 42,
            '004. 序幕': 4,
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(parse_chapter_number(title), expected)
                self.assertEqual(parse_chapter_id(title), expected)

    def test_adapter_payload_is_normalized_without_contract_breaking(self):
        payload = {
            'title': '第八章 归途',
            'content': ['广告', '真正的正文', '真正的正文'],
            'next_url': '9.html',
            'toc_url': '/book/',
        }

        result = self.engine.normalize_payload(payload, 'https://novel.example/book/8.html')

        self.assertEqual(result['page_type'], 'chapter')
        self.assertEqual(result['content'], ['真正的正文'])
        self.assertEqual(result['next_url'], '9.html')
        self.assertIn('recognition', result)

    def test_toc_payload_deduplicates_urls_and_preserves_legacy_fields(self):
        payload = {
            'title': '测试书',
            'chapters': [
                {'raw_title': '第一章 起点', 'url': '1.html'},
                {'raw_title': '第二章 夜航', 'url': '2.html'},
                {'raw_title': '第二章 夜航', 'url': '2.html'},
                {'raw_title': '第三章 回声', 'url': '3.html'},
            ],
        }

        result = self.engine.normalize_payload(payload, 'https://novel.example/book/')

        self.assertEqual(result['page_type'], 'toc')
        self.assertEqual(len(result['chapters']), 3)
        self.assertEqual(result['chapters'][0]['id'], 1)
        self.assertEqual(result['chapters'][0]['url'], 'https://novel.example/book/1.html')

    def test_blocked_payload_is_not_cacheable_and_has_actionable_reason(self):
        payload = RecognitionEngine().analyze_html(
            '<title>Just a moment...</title><div class="challenge-platform">Enable JavaScript and cookies to continue</div>',
            'https://novel.example/book/1.html',
        ).to_payload()

        issue = get_payload_issue(payload)
        self.assertEqual(issue['code'], 'SOURCE_CHALLENGE')
        self.assertFalse(is_cacheable_payload(payload))

    def test_normal_chapter_payload_remains_cacheable(self):
        payload = {
            'page_type': 'chapter',
            'content': ['可靠正文'],
        }

        self.assertIsNone(get_payload_issue(payload))
        self.assertTrue(is_cacheable_payload(payload))

    def test_source_health_cools_down_after_repeated_failure_and_resets_on_success(self):
        now = [100.0]
        tracker = SourceHealthTracker(
            clock=lambda: now[0], failure_threshold=2, base_cooldown_seconds=10,
        )
        url = 'https://example.com/book/1.html'

        tracker.record_failure(url, 'network')
        self.assertEqual(tracker.cooldown_remaining(url), 0)
        tracker.record_failure(url, 'network')
        self.assertEqual(tracker.cooldown_remaining(url), 10)
        tracker.record_success(url)
        self.assertEqual(tracker.snapshot(url)['consecutive_failures'], 0)
        self.assertEqual(tracker.cooldown_remaining(url), 0)

    def test_source_health_immediately_cools_down_after_challenge(self):
        tracker = SourceHealthTracker(clock=lambda: 100.0, challenge_cooldown_seconds=60)
        tracker.record_failure('https://example.com/book/', 'challenge')

        self.assertEqual(tracker.cooldown_remaining('https://example.com/other/'), 60)

    def test_normalization_preserves_blocked_and_cooldown_status(self):
        result = self.engine.normalize_payload({
            'page_type': 'blocked',
            'recognition': {'warnings': ['source_cooldown']},
        })

        self.assertEqual(result['page_type'], 'blocked')
        self.assertEqual(result['recognition_confidence'], 0.99)
        self.assertEqual(result['recognition']['warnings'], ['source_cooldown'])

    def test_cross_source_match_requires_reliable_chapter_evidence(self):
        chapters = [
            {'id': 1, 'title': '第一章 起点', 'url': '1.html'},
            {'id': 2, 'title': '第二章 夜航', 'url': '2.html'},
        ]

        match = find_chapter_match(chapters, 2, '第二章 夜航（修订）')
        self.assertEqual(match['url'], '2.html')
        self.assertEqual(match['match_strategy'], 'id_and_title')
        self.assertIsNone(find_chapter_match(chapters, 2, '完全不同的章节'))
        self.assertIsNone(find_chapter_match(chapters, 9, '无关内容'))


class TestCrawlerRecognitionIntegration(unittest.TestCase):
    def _crawler_with_fixture(self, fixture_name):
        from spider_core import NovelCrawler

        html = (FIXTURE_DIR / fixture_name).read_text(encoding='utf-8')
        crawler = NovelCrawler()
        crawler._fetch_page_smart = lambda url: html
        return crawler

    def test_generic_crawler_returns_canonical_chapter_contract(self):
        crawler = self._crawler_with_fixture('chapter_standard.html')

        result = crawler._general_run_logic('https://novel.example/book/12.html')

        self.assertEqual(result['page_type'], 'chapter')
        self.assertIn('recognition', result)
        self.assertIn('雨落在窗台上。', result['content'])
        self.assertEqual(result['next'], 'https://novel.example/book/13.html')

    def test_generic_crawler_returns_toc_instead_of_fake_chapter(self):
        crawler = self._crawler_with_fixture('toc_standard.html')

        result = crawler._general_run_logic('https://novel.example/book/')

        self.assertEqual(result['page_type'], 'toc')
        self.assertEqual(len(result['chapters']), 4)
        self.assertGreaterEqual(result['recognition_confidence'], 0.6)

    def test_generic_crawler_joins_paginated_chapter(self):
        from spider_core import NovelCrawler

        first = (FIXTURE_DIR / 'paginated_chapter_1.html').read_text(encoding='utf-8')
        second = (FIXTURE_DIR / 'paginated_chapter_2.html').read_text(encoding='utf-8')
        crawler = NovelCrawler()
        crawler._fetch_page_smart = lambda url: second if url.endswith('10_2.html') else first

        result = crawler._general_run_logic('https://novel.example/book/10.html')

        self.assertIn('第一页的正文从清晨开始。', result['content'])
        self.assertIn('第二页的正文继续展开。', result['content'])
        self.assertEqual(result['next'], 'https://novel.example/book/11.html')

    def test_generic_toc_joins_paginated_catalog_and_deduplicates_chapters(self):
        from spider_core import NovelCrawler

        first = (FIXTURE_DIR / 'toc_paginated_1.html').read_text(encoding='utf-8')
        second = (FIXTURE_DIR / 'toc_paginated_2.html').read_text(encoding='utf-8')
        crawler = NovelCrawler()
        crawler._fetch_page_smart = lambda url: second if url.endswith('catalog_2.html') else first

        result = crawler._general_toc_logic('https://novel.example/book/catalog.html')

        self.assertEqual([chapter['id'] for chapter in result['chapters']], [1, 2, 3, 4])
        self.assertEqual(result['chapters'][-1]['url'], 'https://novel.example/book/4.html')

    def test_standardization_sorts_only_within_each_numbered_volume(self):
        from spider_core import NovelCrawler

        html = (FIXTURE_DIR / 'toc_volumes.html').read_text(encoding='utf-8')
        result = RecognitionEngine().analyze_html(html, 'https://novel.example/book/')
        chapters = NovelCrawler()._standardize_chapters(result.chapters)

        self.assertEqual([chapter['id'] for chapter in chapters], [1, 2, 1, 2])
        self.assertEqual([chapter['volume'] for chapter in chapters], [
            '第一卷 初入江湖', '第一卷 初入江湖',
            '第二卷 风云再起', '第二卷 风云再起',
        ])

    def test_fetch_uses_standard_client_after_browser_client_network_failure(self):
        from spider_core import NovelCrawler

        class Response:
            content = b'<html><body><p>fallback page</p></body></html>'

        crawler = NovelCrawler()
        with patch('spider_core.cffi_requests.get', side_effect=OSError('DNS failure')) as browser_get:
            with patch('spider_core.requests.get', return_value=Response()) as standard_get:
                html = crawler._fetch_page_smart('https://novel.example/book/1.html', retry=1, timeout=1)

        self.assertIn('fallback page', html)
        browser_get.assert_called_once()
        standard_get.assert_called_once()


class TestTocPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(
            __name__,
            template_folder=str(Path(__file__).resolve().parents[1] / 'templates'),
        )

    def test_volume_headings_render_and_repeated_chapter_ids_are_not_marked_read(self):
        toc = {
            'title': '测试书',
            'chapters': [
                {'id': 1, 'name': '起点', 'url': 'v1-1', 'volume': '第一卷'},
                {'id': 1, 'name': '重逢', 'url': 'v2-1', 'volume': '第二卷'},
            ],
        }
        with self.app.test_request_context('/'):
            html = render_template(
                'toc.html', toc=toc, toc_url='https://example.com/book/', db_key='book',
                progress={'last_read_index': 1, 'last_read_url': 'v1-1'},
            )

        self.assertIn('第一卷', html)
        self.assertIn('第二卷', html)
        self.assertEqual(html.count('class="chapter-item is-last-read"'), 1)
        self.assertNotIn('chapter-item is-read', html)


if __name__ == '__main__':
    unittest.main()
