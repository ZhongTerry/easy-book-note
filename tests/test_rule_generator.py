"""规则生成管线测试：mock 掉 LLM，验证验收门槛、频控与入库状态机。"""
import json
import os
import unittest
from unittest.mock import Mock, patch

import managers
from recognition.rule_engine import extract_chapter
from services import rule_generator, site_rules
from tests.test_rule_engine import RULE, BASE, bqquge_fetcher, load_fixture


class FakeCrawler:
    """只暴露生成管线用到的两个方法。"""

    def __init__(self, baseline='generic'):
        self.baseline_mode = baseline

    def _fetch_page_smart(self, url):
        return bqquge_fetcher(url)

    def _general_run_logic(self, url):
        if self.baseline_mode == 'generic':
            result = extract_chapter(load_fixture('bqquge_page1.html'), RULE, BASE,
                                     fetcher=bqquge_fetcher)
            return result['payload']
        return None  # 通用引擎失败 -> 走跨章节自洽验收


def synthetic_chapter(n, paragraphs=8):
    body = ''.join(
        f'<p>第{n}章第{i}段：' + '这是一段足够长的正文内容用于跨章节自洽检查。' * 3 + '</p>'
        for i in range(paragraphs)
    )
    return (f'<html><body><h1>第{n}章 标题</h1><div class="content">{body}</div>'
            f'<a href="/book/1/{n - 1}.html">上一章</a>'
            f'<a href="/book/1/{n + 1}.html">下一章</a></body></html>')


SYNTHETIC_RULE = {'chapter': {'content_selector': 'div.content', 'paragraph_selector': 'p',
                              'title_selector': 'h1', 'min_paragraphs': 5},
                  'navigation': {}}


class RuleGenerationTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db_patch = patch.object(managers, 'DB_PATH',
                                os.path.join(self.temp.name, 't.sqlite'))
        db_patch.start()
        self.addCleanup(db_patch.stop)


class TestGenerateRule(RuleGenerationTestBase):
    def test_baseline_path_activates_rule(self):
        crawler = FakeCrawler(baseline='generic')
        with patch.object(rule_generator, '_call_llm', return_value=json.loads(json.dumps(RULE))):
            result = rule_generator.generate_rule(BASE, crawler)
        self.assertEqual(result['status'], 'active', result)
        self.assertGreaterEqual(result['score'], 0.85)
        self.assertEqual(result['paragraphs'], 54)
        self.assertIsNotNone(site_rules.get_active_rule(result['domain']))

    def test_cross_page_path_when_generic_fails(self):
        crawler = FakeCrawler(baseline=None)
        rule = json.loads(json.dumps(SYNTHETIC_RULE))
        with patch.object(rule_generator, '_call_llm', return_value=rule), \
             patch.object(crawler, '_fetch_page_smart',
                          side_effect=lambda url: synthetic_chapter(int(url.rsplit('/', 1)[-1].split('.')[0]))):
            result = rule_generator.generate_rule('https://synthetic.test/book/1/500.html', crawler)
        self.assertEqual(result['status'], 'active', result)
        self.assertGreaterEqual(result['score'], 0.85)

    def test_rejected_rule_is_not_stored(self):
        crawler = FakeCrawler(baseline='generic')
        broken = json.loads(json.dumps(RULE))
        broken['chapter']['content_selector'] = 'div.nonexistent'
        with patch.object(rule_generator, '_call_llm', return_value=broken):
            result = rule_generator.generate_rule(BASE, crawler)
        self.assertEqual(result['status'], 'error')
        self.assertIn('验收', result['message'])
        self.assertIsNone(site_rules.get_active_rule('www.bqquge.org'))

    def test_static_validation_rejects_malformed_rule(self):
        crawler = FakeCrawler(baseline='generic')
        with patch.object(rule_generator, '_call_llm', return_value={'chapter': {}}):
            result = rule_generator.generate_rule(BASE, crawler)
        self.assertEqual(result['status'], 'error')
        self.assertIn('静态校验', result['message'])

    def test_unconfigured_llm_reports_clear_error(self):
        crawler = FakeCrawler(baseline='generic')
        # 测试进程没有 RULE_LLM_* 环境变量，功能应整体关闭并给出可定位错误
        result = rule_generator.generate_rule(BASE, crawler)
        self.assertEqual(result['status'], 'error')
        self.assertIn('RULE_LLM_BASE_URL', result['message'])


class TestAutoTrigger(RuleGenerationTestBase):
    def test_disabled_by_default(self):
        submit = Mock(return_value='tid')
        self.assertIsNone(rule_generator.maybe_autogenerate('https://x.example.test/a/1.html', submit))
        submit.assert_not_called()

    @patch.object(rule_generator, '_AUTO_TRIGGER', True)
    def test_domain_cooldown_and_daily_limit(self):
        submit = Mock(return_value='tid')
        first = rule_generator.maybe_autogenerate('https://a.example.test/1/1.html', submit)
        self.assertEqual(first, 'tid')
        # 同域名 24h 冷却
        self.assertIsNone(rule_generator.maybe_autogenerate('https://a.example.test/1/2.html', submit))
        # 不同域名可以，但全局每日上限（默认 10）用尽后拒绝
        for i in range(20):
            rule_generator.maybe_autogenerate(f'https://d{i}.example.test/1/1.html', submit)
        self.assertEqual(submit.call_count, 1 + 9)  # a + 前 9 个 d 域名

    @patch.object(rule_generator, '_AUTO_TRIGGER', True)
    def test_skips_domains_with_active_rule(self):
        site_rules.upsert_rule('active.example.test', RULE, accept_score=0.95)
        submit = Mock(return_value='tid')
        self.assertIsNone(rule_generator.maybe_autogenerate(
            'https://active.example.test/1/1.html', submit))
        submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
