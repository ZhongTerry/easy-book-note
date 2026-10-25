"""站点规则层测试：执行器、验收打分与存储生命周期。

bqquge 四页 fixture 是线上真实页面，离线复刻了"AI 生成规则 + 分页合并
= 54 段与通用引擎一致"的实验结论。
"""
import json
import os
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import managers
from recognition.rule_engine import apply_rule, extract_chapter, validate_rule, validate_rule_chapter
from recognition.chapter_numbers import parse_chapter_number
from services import site_rules

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'site_rules')
BASE = 'https://www.bqquge.org/671/676855'
RULE = json.load(open(os.path.join(FIXTURES, 'bqquge_rule.json'), encoding='utf-8'))


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
        return f.read()


def bqquge_fetcher(url):
    mapping = {'676855': 1, '676855-2': 2, '676855-3': 3, '676855-4': 4}
    for key, page in mapping.items():
        if url.endswith(key):
            return load_fixture(f'bqquge_page{page}.html')
    return None


class TestRuleEngineBqquge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = extract_chapter(load_fixture('bqquge_page1.html'), RULE, BASE,
                                     fetcher=bqquge_fetcher)

    def test_paginated_merge_matches_engine_baseline(self):
        self.assertEqual(self.result['trace']['pages'], 4)
        payload = self.result['payload']
        self.assertEqual(len(payload['content']), 54)
        self.assertEqual(payload['title'], '第一百四十八章 良心店铺（第二更）')
        self.assertTrue(payload['content'][0].startswith('接下来的几天'))
        self.assertTrue(payload['content'][-1].startswith('「是啊'))

    def test_navigation_resolved(self):
        payload = self.result['payload']
        self.assertEqual(payload['prev'], 'https://www.bqquge.org/671/676854')
        self.assertEqual(payload['next'], 'https://www.bqquge.org/671/676856')
        self.assertEqual(payload['toc_url'], 'https://www.bqquge.org/671#dir')

    def test_validate_rule_chapter_perfect_for_own_baseline(self):
        payload = self.result['payload']
        score = validate_rule_chapter(load_fixture('bqquge_page1.html'), RULE, BASE,
                                      fetcher=bqquge_fetcher,
                                      baseline_paras=payload['content'],
                                      baseline_title=payload['title'],
                                      baseline_prev=payload['prev'],
                                      baseline_next=payload['next'])
        self.assertGreaterEqual(score, 0.99)

    def test_validate_rule_chapter_rejects_broken_selector(self):
        broken = json.loads(json.dumps(RULE))
        broken['chapter']['content_selector'] = 'div.nonexistent'
        payload = self.result['payload']
        score = validate_rule_chapter(load_fixture('bqquge_page1.html'), broken, BASE,
                                      fetcher=bqquge_fetcher,
                                      baseline_paras=payload['content'],
                                      baseline_title=payload['title'])
        self.assertEqual(score, 0.0)


class TestRuleEngineSemantics(unittest.TestCase):
    def test_next_page_versus_next_chapter(self):
        # 同一页里：文本"下一页"且含章节编号 -> 分页；"下一章"不含编号 -> 换章；
        # 文本"下一页"但指向别的章节 -> 两类都不是（防误拼接）。
        html = '''<html><body><div class="content">
            <p>段落一</p><p>段落二</p><p>段落三</p><p>段落四</p><p>段落五</p>
        </div>
        <a href="/book/100/500.html">下一页</a>
        <a href="/book/100/500-2.html">下一页</a>
        <a href="/book/100/501.html">下一章</a>
        <a href="/book/100/499.html">上一章</a>
        </body></html>'''
        rule = {'chapter': {'content_selector': 'div.content', 'paragraph_selector': 'p',
                            'min_paragraphs': 3},
                'navigation': {}}
        result = apply_rule(html, rule, 'https://example.test/book/100/500.html')
        self.assertIsNotNone(result)
        self.assertEqual(result['next_page_url'], 'https://example.test/book/100/500-2.html')
        self.assertEqual(result['next'], 'https://example.test/book/100/501.html')
        self.assertEqual(result['prev'], 'https://example.test/book/100/499.html')

    def test_br_split_paragraphs(self):
        html = '<div id="c">第一段<br>第二段<br><br>第三段</div>'
        rule = {'chapter': {'content_selector': '#c', 'paragraph_selector': 'br-split',
                            'min_paragraphs': 2}}
        result = apply_rule(html, rule, 'https://example.test/1.html')
        self.assertEqual(result['paragraphs'], ['第一段', '第二段', '第三段'])

    def test_noise_selectors_removed(self):
        html = '''<div id="c"><p>正文一</p><div class="guess">猜你喜欢: 另一本书名</div>
                  <p>正文二</p><p>正文三</p><p>正文四</p></div>'''
        rule = {'chapter': {'content_selector': '#c', 'paragraph_selector': 'p',
                            'noise_selectors': ['.guess'], 'min_paragraphs': 2}}
        result = apply_rule(html, rule, 'https://example.test/1.html')
        self.assertEqual(result['paragraphs'], ['正文一', '正文二', '正文三', '正文四'])

    def test_below_min_paragraphs_returns_none(self):
        html = '<div id="c"><p>只有一段</p></div>'
        rule = {'chapter': {'content_selector': '#c', 'paragraph_selector': 'p',
                            'min_paragraphs': 5}}
        trace = {}
        self.assertIsNone(apply_rule(html, rule, 'https://example.test/1.html', trace))
        self.assertEqual(trace['reason'], 'below_min_paragraphs')

    def test_invalid_selector_never_raises(self):
        rule = {'chapter': {'content_selector': 'div[:bogus', 'min_paragraphs': 1}}
        trace = {}
        self.assertIsNone(apply_rule('<div>x</div>', rule, 'https://e.test/1.html', trace))
        self.assertEqual(trace['reason'], 'content_selector_missed')

    def test_chapter_token_forms(self):
        for url, token in [
            ('https://s.org/671/676855', '676855'),
            ('https://s.org/671/676855-2', '676855'),
            ('https://s.org/xs/1/52994575.html', '52994575'),
            ('https://s.org/xs/1/52994575_2.html', '52994575'),
            ('https://s.org/book.php?id=99', ''),
        ]:
            self.assertEqual(extract_chapter_token(url), token)


def extract_chapter_token(url):
    from recognition.rule_engine import _chapter_token
    return _chapter_token(url)


class TestSiteRulesStore(unittest.TestCase):
    def setUp(self):
        self.temp = __import__('tempfile').TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(managers, 'DB_PATH',
                               os.path.join(self.temp.name, 't.sqlite'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_upsert_and_lookup(self):
        rule_id = site_rules.upsert_rule('www.example.test', RULE,
                                         accept_score=0.95, confidence=0.8,
                                         sample_url=BASE)
        self.assertIsNotNone(rule_id)
        found = site_rules.get_active_rule('www.example.test')
        self.assertIsNotNone(found)
        rule, row = found
        self.assertEqual(rule['chapter']['content_selector'], RULE['chapter']['content_selector'])
        self.assertEqual(row['accept_score'], 0.95)
        self.assertIsNone(site_rules.get_active_rule('other.example.test'))

    def test_low_score_becomes_unverified(self):
        site_rules.upsert_rule('low.example.test', RULE, accept_score=0.7)
        found = site_rules.get_active_rule('low.example.test')
        self.assertIsNone(found)  # unverified 不参与命中

    def test_consecutive_failures_demote_rule(self):
        rule_id = site_rules.upsert_rule('demote.example.test', RULE, accept_score=0.95)
        for _ in range(site_rules._DEMOTE_AFTER_CONSECUTIVE_FAILURES):
            site_rules.record_rule_usage(rule_id, False, domain='demote.example.test')
        self.assertIsNone(site_rules.get_active_rule('demote.example.test'))
        # 成功回 reappearance：重新入库即恢复
        site_rules.upsert_rule('demote.example.test', RULE, accept_score=0.95)
        self.assertIsNotNone(site_rules.get_active_rule('demote.example.test'))

    def test_success_resets_failure_counter(self):
        rule_id = site_rules.upsert_rule('reset.example.test', RULE, accept_score=0.95)
        for _ in range(4):
            site_rules.record_rule_usage(rule_id, False)
        site_rules.record_rule_usage(rule_id, True)
        for _ in range(4):
            site_rules.record_rule_usage(rule_id, False)
        self.assertIsNotNone(site_rules.get_active_rule('reset.example.test'))

    def test_rule_json_survives_roundtrip(self):
        site_rules.upsert_rule('round.example.test', RULE, accept_score=0.95)
        stored = site_rules.get_rule_json(
            site_rules.get_active_rule('round.example.test')[1]['id'])
        self.assertEqual(stored, RULE)


if __name__ == '__main__':
    unittest.main()
