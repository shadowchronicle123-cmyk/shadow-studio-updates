"""Offline Web OAuth enrollment tests: no Google calls or real secrets."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from authorize_youtube_web import Enrollment, validate_origin


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

    def test_success_saves_disabled_config(self):
        state = parse_qs(urlsplit(self.worker.begin('k' * 48)).query)['state'][0]
        http = Mock()
        http.post.return_value.status_code = 200
        http.post.return_value.json.return_value = {
            'access_token': 'fake-token', 'refresh_token': 'fake-refresh',
            'scope': 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly'}
        self.worker.http = http
        with patch('authorize_youtube_web.write_config') as write:
            self.worker.finish(state, 'fake-code')
        self.assertEqual(write.call_args.args[4], False)
        self.assertEqual(http.post.call_args.kwargs['data']['redirect_uri'],
                         'https://oauth.example.test/oauth/callback')


if __name__ == '__main__':
    unittest.main()
