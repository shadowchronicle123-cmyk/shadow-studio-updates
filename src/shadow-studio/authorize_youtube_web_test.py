"""Offline Web OAuth enrollment tests: no Google calls or real secrets."""
import json
import tempfile
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from authorize_youtube_web import Enrollment, handler_for, validate_origin


class WebOAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'youtube.json'
        self.client = {'client_id': 'test-client', 'client_secret': 'test-secret',
                       'redirect_uris': ['https://oauth.example.test/oauth/callback']}
        self.worker = Enrollment(self.client, 'https://oauth.example.test', self.output,
                                 'UC' + 'a' * 22, 'not-made-for-kids', 'k' * 48)

    def test_https_and_exact_callback(self):
        for origin in ('http://example.test', 'https://example.test/path',
                       'https://user@example.test', 'https://example.test?x=1'):
            with self.assertRaises(ValueError):
                validate_origin(origin)
        with self.assertRaises(ValueError):
            Enrollment(self.client, 'https://other.example.test', self.output,
                       'UC' + 'a' * 22, 'not-made-for-kids', 'k' * 48)

    def test_pkce_and_single_use(self):
        with self.assertRaises(ValueError):
            self.worker.begin('wrong')
        url = self.worker.begin('k' * 48)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['redirect_uri'], ['https://oauth.example.test/oauth/callback'])
        self.assertEqual(query['access_type'], ['offline'])
        self.assertEqual(query['prompt'], ['consent'])
        with self.assertRaises(ValueError):
            self.worker.begin('k' * 48)
        with self.assertRaises(ValueError):
            self.worker.finish('invalid', 'fake')
        self.assertFalse(self.output.exists())

    def test_callback_consumed_on_failure(self):
        query = parse_qs(urlsplit(self.worker.begin('k' * 48)).query)
        state = query['state'][0]
        http = Mock()
        http.post.return_value.status_code = 400
        self.worker.http = http
        with self.assertRaises(ValueError):
            self.worker.finish(state, 'fake-code')
        with self.assertRaises(ValueError):
            self.worker.finish(state, 'fake-code')
        self.assertFalse(self.output.exists())

    def test_expired_state_can_restart(self):
        with patch('authorize_youtube_web.time.monotonic', return_value=0):
            original = parse_qs(urlsplit(self.worker.begin('k' * 48)).query)['state'][0]
        with patch('authorize_youtube_web.time.monotonic', return_value=301):
            with self.assertRaises(ValueError):
                self.worker.finish(original, 'fake-code')
            self.assertIn('state=', self.worker.begin('k' * 48))

    def test_http_callback_and_worker_output(self):
        http = Mock()
        http.post.return_value.status_code = 200
        http.post.return_value.json.return_value = {
            'token_type': 'Bearer', 'access_token': 'fake-token', 'refresh_token': 'fake-refresh',
            'scope': 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly'}
        http.get.return_value.status_code = 200
        http.get.return_value.json.return_value = {'items': [{'id': 'UC' + 'a' * 22}]}
        self.worker.http = http
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.worker))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request('GET', '/oauth/start?key=unwanted')
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        conn.request('POST', '/oauth/start', urlencode({'key': 'k' * 48}),
                     {'Origin': self.worker.origin, 'Content-Type': 'application/x-www-form-urlencoded'})
        response = conn.getresponse()
        self.assertEqual(response.status, 303)
        state = parse_qs(urlsplit(response.getheader('Location')).query)['state'][0]
        response.read()
        callback = '/oauth/callback?' + urlencode({'state': state, 'code': 'fake-code', 'authuser': '0',
                                                  'scope': 'granted-scope', 'prompt': 'consent'})
        conn.request('GET', callback)
        response = conn.getresponse()
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader('Location'), '/oauth/complete')
        self.assertNotIn(b'fake-code', response.read())
        self.assertEqual(json.loads(self.output.read_text())['SHADOW_YOUTUBE_PRIVATE_AUTOMATION'], '0')
        conn.request('GET', callback)
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        http.post.assert_called_once()
        http.get.assert_called_once()

    def test_success_saves_disabled_config(self):
        state = parse_qs(urlsplit(self.worker.begin('k' * 48)).query)['state'][0]
        http = Mock()
        http.post.return_value.status_code = 200
        http.post.return_value.json.return_value = {
            'token_type': 'Bearer', 'access_token': 'fake-token', 'refresh_token': 'fake-refresh',
            'scope': 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly'}
        self.worker.http = http
        with patch('authorize_youtube_web.write_config') as write:
            self.worker.finish(state, 'fake-code')
        self.assertEqual(write.call_args.args[4], False)
        self.assertEqual(http.post.call_args.kwargs['data']['redirect_uri'],
                         'https://oauth.example.test/oauth/callback')


if __name__ == '__main__':
    unittest.main()
