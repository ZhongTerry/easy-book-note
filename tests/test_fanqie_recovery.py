import unittest
from unittest.mock import Mock, patch

import requests
from adapters import fanqie_adapter as adapter


class TestFanqieRecovery(unittest.TestCase):
    def setUp(self):
        state = adapter._breaker.copy()
        self.addCleanup(adapter._breaker.update, state)
        adapter._breaker.update(consecutive=0, until=0)

    def test_business_failure_trips_breaker_even_with_http_200(self):
        response = Mock(status_code=200)
        response.json.return_value = {'code': 1}
        client = adapter.FanqieLocalAdapter()
        with patch.object(adapter.requests, 'get', return_value=response) as get:
            for _ in range(4):
                client._api_get('/get_content', {'item_id': '123'})
        self.assertEqual(get.call_count, 3)
        self.assertTrue(adapter._breaker_open())

    def test_public_directory_works_during_local_breaker_without_leaking_token(self):
        adapter._breaker['until'] = float('inf')
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"code":0,"data":{"chapterListWithVolume":[[{"itemId":"123","title":"Chapter 1"}]]}}'
        client = adapter.FanqieLocalAdapter()
        with patch.object(adapter.requests, 'get', return_value=response) as get:
            chapters = client._fetch_toc_list('456')
        self.assertEqual(chapters[0]['item_id'], '123')
        self.assertNotIn('Authorization', get.call_args.kwargs['headers'])
