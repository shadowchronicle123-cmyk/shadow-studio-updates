"""One-time phone OAuth enrollment, served behind an HTTPS reverse proxy.

Run on a trusted host with SHADOW_OAUTH_PUBLIC_ORIGIN=https://your-domain.
Only the exact /oauth/callback URI is registered with Google. Never expose
this HTTP backend directly to the Internet; TLS must terminate at the proxy.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlsplit, parse_qs

import requests
from authorize_youtube import AUDIENCES, SCOPES, write_config
from private_upload import TOKEN, verify_channel

AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
MAX_QUERY = 4096


def validate_origin(origin):
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/') or parsed.port == 0):
        raise ValueError('Public origin must be an HTTPS origin without a path')
    return origin.rstrip('/')


def client_config(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))['web']
    if (data.get('auth_uri') not in (AUTH, 'https://accounts.google.com/o/oauth2/auth')
            or data.get('token_uri') != TOKEN or not data.get('client_id')
            or not data.get('client_secret')):
        raise ValueError('Expected a Google Web application OAuth client')
    return data


class Enrollment:
    def __init__(self, client, origin, output, channel, audience, setup_key, http=requests):
        self.origin = validate_origin(origin)
        self.callback = self.origin + '/oauth/callback'
        if self.callback not in client.get('redirect_uris', []):
            raise ValueError('Register the exact HTTPS callback in the Web OAuth client')
        if audience not in AUDIENCES:
            raise ValueError('Invalid audience')
        if Path(output).exists():
            raise ValueError('Credential output already exists')
        if len(setup_key) < 32:
            raise ValueError('Use a random setup key of at least 32 characters')
        self.client, self.output, self.channel = client, Path(output), channel
        self.audience, self.setup_key, self.http = audience, setup_key, http
        self.pending = {}
        self.used = False
        self.lock = threading.Lock()

    def begin(self, key):
        if not hmac.compare_digest(key.encode(), self.setup_key.encode()):
            raise ValueError('Invalid setup key')
        with self.lock:
            self.pending = {state: entry for state, entry in self.pending.items()
                            if entry[1] > time.monotonic()}
            if self.used or self.pending:
                raise ValueError('Enrollment already started')
            state = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            self.pending[state] = (verifier, time.monotonic() + 300)
        params = dict(client_id=self.client['client_id'], redirect_uri=self.callback,
                      response_type='code', scope=' '.join(SCOPES), access_type='offline',
                      prompt='consent', state=state, code_challenge=challenge,
                      code_challenge_method='S256', include_granted_scopes='false')
        return AUTH + '?' + urlencode(params)

    def finish(self, state, code):
        with self.lock:
            entry = self.pending.pop(state, None)
            if not entry or self.used or time.monotonic() > entry[1]:
                raise ValueError('Expired or already used authorization')
            self.used = True  # fail closed: restart the server to retry
        verifier = entry[0]
        try:
            response = self.http.post(TOKEN, data=dict(
                client_id=self.client['client_id'],
                client_secret=self.client['client_secret'], code=code,
                code_verifier=verifier, grant_type='authorization_code',
                redirect_uri=self.callback), timeout=(8, 30), allow_redirects=False)
            if response.status_code != 200:
                raise ValueError('Google token exchange failed')
            payload = response.json()
            if (not isinstance(payload, dict) or payload.get('token_type', '').lower() != 'bearer'
                    or not all(isinstance(payload.get(field), str) and payload[field]
                               for field in ('access_token', 'refresh_token'))):
                raise ValueError('Missing access token')
            granted = set(payload.get('scope', '').split())
            if not set(SCOPES).issubset(granted):
                raise ValueError('Required YouTube scopes not granted')
            class Credentials:
                token = payload['access_token']
                refresh_token = payload.get('refresh_token')
                client_id = self.client['client_id']
                client_secret = self.client['client_secret']
            write_config(self.output, Credentials, self.channel, self.audience, False,
                         verify=lambda token, channel, _: verify_channel(token, channel, self.http))
        except Exception:
            raise ValueError('Authorization failed; restart enrollment and inspect server diagnostics') from None


def handler_for(enrollment):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # Never log paths, authorization codes, query strings or setup keys

        def reply(self, status, message, location=None):
            self.send_response(status)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; form-action 'self'; frame-ancestors 'none'")
            if location:
                self.send_header('Location', location)
            body = ('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>Shadow Studio authorization</title><p>' + message + '</p>').encode()
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if len(self.path) > MAX_QUERY:
                return self.reply(414, 'Request too long')
            try:
                url = urlsplit(self.path)
            except ValueError:
                return self.reply(400, 'Invalid request')
            if url.path == '/oauth/start':
                if url.query:
                    return self.reply(400, 'Use the enrollment form')
                return self.reply(200, 'Enter your private setup key.</p>'
                                  '<form method="post" action="/oauth/start">'
                                  '<label>Setup key <input name="key" type="password" required></label>'
                                  '<button>Continue to Google</button></form><p>')
            if url.path == '/oauth/complete' and not url.query and enrollment.output.is_file():
                return self.reply(200, 'Shadow Studio authorization saved. Automatic uploads remain disabled.')
            if url.path == '/oauth/callback':
                query = parse_qs(url.query)
                if query.get('error'):
                    return self.reply(400, 'Google authorization was declined')
                if not {'state', 'code'} <= query.keys() or any(len(v) != 1 for v in query.values()):
                    return self.reply(400, 'Invalid callback')
                try:
                    enrollment.finish(query['state'][0], query['code'][0])
                except ValueError:
                    return self.reply(400, 'Authorization failed. Contact the server operator.')
                return self.reply(303, 'Authorization saved', '/oauth/complete')
            return self.reply(404, 'Not found')
        def do_POST(self):
            if self.path != '/oauth/start':
                return self.reply(404, 'Not found')
            if self.headers.get('Origin') != enrollment.origin:
                return self.reply(403, 'Invalid enrollment origin')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_QUERY or self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                    raise ValueError()
                query = parse_qs(self.rfile.read(length).decode('ascii'), strict_parsing=True)
                if set(query) != {'key'} or len(query['key']) != 1:
                    raise ValueError()
                destination = enrollment.begin(query['key'][0])
            except ValueError:
                return self.reply(403, 'Enrollment unavailable')
            return self.reply(303, 'Redirecting to Google', destination)

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--client', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--channel-id', required=True)
    p.add_argument('--audience', choices=AUDIENCES, required=True)
    p.add_argument('--port', type=int, default=8765)
    args = p.parse_args()
    origin = os.environ['SHADOW_OAUTH_PUBLIC_ORIGIN']
    key = os.environ['SHADOW_OAUTH_SETUP_KEY']
    enrollment = Enrollment(client_config(args.client), origin, args.output,
                            args.channel_id, args.audience, key)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(enrollment))
    print('OAuth enrollment server listening on localhost. Configure HTTPS proxy to /oauth/.')
    print('Open PUBLIC_ORIGIN/oauth/start and enter the setup key privately.')
    server.serve_forever()


if __name__ == '__main__':
    main()
