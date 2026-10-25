"""页面质量反馈链路测试：登记、配额、任务提交与结果回写、按域名清缓存。"""
import json
import os
import unittest
from unittest.mock import Mock, patch

import managers
from services import feedback as feedback_service
from services import rule_generator
from tests.test_rule_engine import RULE, load_fixture


class FeedbackTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db_patch = patch.object(managers, 'DB_PATH',
                                os.path.join(self.temp.name, 't.sqlite'))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        self.submit = Mock(return_value='task-1')


class TestFeedbackRecord(FeedbackTestBase):
    def test_record_and_outcome_roundtrip(self):
        fid = feedback_service.record_feedback(
            'https://www.bqquge.org/671/676855', 'bk', 'ztrztr',
            domain='www.bqquge.org', note='混入推荐')
        self.assertIsNotNone(fid)
        rows = feedback_service.list_feedback()
        self.assertEqual(rows[0]['status'], 'pending')

        feedback_service.update_feedback_outcome(fid, {
            'status': 'active', 'domain': 'www.bqquge.org',
            'score': 0.97, 'paragraphs': 54,
        })
        row = feedback_service.list_feedback()[0]
        self.assertEqual(row['status'], 'rule_updated')
        self.assertEqual(row['outcome']['score'], 0.97)

    def test_failed_generation_marks_failed(self):
        fid = feedback_service.record_feedback('https://x.example.test/1.html', '', 'u',
                                               domain='x.example.test')
        feedback_service.update_feedback_outcome(fid, {'status': 'error', 'message': '抓取失败'})
        self.assertEqual(feedback_service.list_feedback()[0]['status'], 'failed')


class TestCachePurgeDomain(FeedbackTestBase):
    def test_purge_only_matching_domain(self):
        managers.cache.set('https://www.aaa.test/book/1.html', {'p': 1})
        managers.cache.set('https://www.aaa.test/book/2.html', {'p': 2})
        managers.cache.set('https://www.bbb.test/book/1.html', {'p': 3})

        removed = managers.cache.purge_domain('www.aaa.test')

        self.assertEqual(removed, 2)
        self.assertIsNone(managers.cache.get('https://www.aaa.test/book/1.html'))
        self.assertIsNone(managers.cache.get('https://www.aaa.test/book/2.html'))
        self.assertIsNotNone(managers.cache.get('https://www.bbb.test/book/1.html'))

    def test_generate_rule_purges_cache_on_activation(self):
        crawler = Mock()
        crawler._general_run_logic.return_value = None
        fixture_map = {'676855': 1, '676855-2': 2, '676855-3': 3, '676855-4': 4}

        def fake_no_redirect(url):
            for key, page in fixture_map.items():
                if url.endswith(key):
                    return Mock(status_code=200, headers={},
                                content=load_fixture(f'bqquge_page{page}.html').encode('utf-8'))
            return Mock(status_code=404, headers={}, content=b'')

        crawler._fetch_page_no_redirect.side_effect = fake_no_redirect
        crawler._decode_page_response.side_effect = lambda resp: resp.content.decode('utf-8')

        managers.cache.set('https://www.bqquge.org/671/676855', {'stale': True})

        rule = json.loads(json.dumps(RULE))
        with patch.object(rule_generator, '_call_llm', return_value=rule), \
             patch.object(rule_generator, '_score_with_baseline',
                          return_value=(0.95, {'baseline': 'generic'})), \
             patch('shared.is_safe_url', return_value=True):
            result = rule_generator.generate_rule(
                'https://www.bqquge.org/671/676855', crawler)
        self.assertEqual(result['status'], 'active')
        self.assertGreaterEqual(result['cache_purged'], 1)
        self.assertIsNone(managers.cache.get('https://www.bqquge.org/671/676855'))


if __name__ == '__main__':
    unittest.main()
