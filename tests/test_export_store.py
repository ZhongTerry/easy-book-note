import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from services.export_store import save_task


def publish(filename, prefix):
    for number in range(6):
        save_task(filename, f'{prefix}-{number}', {'status': 'paused', 'title': '测试'})


class TestExportStore(unittest.TestCase):
    def test_fulltext_redis_preserves_authentication_and_database(self):
        import managers
        from unittest.mock import Mock, patch
        manager = managers.FullTextCacheManager.__new__(managers.FullTextCacheManager)
        client = Mock()
        url = 'redis://reader:example-password@localhost:6379/4'
        with patch.dict('os.environ', {'REDIS_URL': url}), \
                patch.object(managers.redis, 'from_url', return_value=client) as connect:
            manager._init_redis()
        self.assertTrue(manager.use_redis)
        self.assertEqual(connect.call_args.args[0], url.replace('localhost', '127.0.0.1'))
        client.ping.assert_called_once()

    def test_processes_merge_without_corruption_or_lost_tasks(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'tasks.json'
            context = multiprocessing.get_context('spawn')
            workers = [context.Process(target=publish, args=(str(path), str(i))) for i in range(3)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=20)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(len(json.loads(path.read_text(encoding='utf-8'))), 18)

    def test_corruption_is_reported_without_overwriting_original(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'tasks.json'
            original = b'{"broken":'
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                save_task(path, 'new', {})
            self.assertEqual(path.read_bytes(), original)

    def test_unserializable_task_keeps_last_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'tasks.json'
            save_task(path, 'old', {'status': 'completed'})
            original = path.read_bytes()
            with self.assertRaises(TypeError):
                save_task(path, 'bad', {'object': object()})
            self.assertEqual(path.read_bytes(), original)
