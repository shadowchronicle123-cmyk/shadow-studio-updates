"""Authorize the existing ShadowChronicles channel from an owner-controlled desktop."""
import argparse
import json
import os
from pathlib import Path
import requests

from private_upload import TOKEN, verify_channel

SCOPES = ['https://www.googleapis.com/auth/youtube.upload',
          'https://www.googleapis.com/auth/youtube.readonly']
AUDIENCES = ('made-for-kids', 'not-made-for-kids')


def write_config(output, credentials, channel_id, audience, enable_daily, *, verify=verify_channel):
    if not all((credentials.token, credentials.refresh_token, credentials.client_id, credentials.client_secret)):
        raise ValueError('Google did not grant offline access. Verify the OAuth consent setup and try again.')
    if audience not in AUDIENCES:
        raise ValueError('Choose the audience for the private uploads')
    verify(credentials.token, channel_id, requests)
    settings = {
        'SHADOW_YOUTUBE_CHANNEL_ID': channel_id,
        'SHADOW_YOUTUBE_AUDIENCE': audience,
        'SHADOW_YOUTUBE_PRIVATE_AUTOMATION': '1' if enable_daily else '0',
        'SHADOW_YOUTUBE_CLIENT_ID': credentials.client_id,
        'SHADOW_YOUTUBE_CLIENT_SECRET': credentials.client_secret,
        'SHADOW_YOUTUBE_REFRESH_TOKEN': credentials.refresh_token,
    }
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(settings, stream, indent=2)
            stream.write('\n')
    except OSError:
        Path(output).unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', required=True, type=Path, help='Google Desktop OAuth client JSON')
    parser.add_argument('--output', required=True, type=Path, help='New private file outside the repository')
    parser.add_argument('--channel-id', required=True)
    parser.add_argument('--audience', required=True, choices=AUDIENCES)
    parser.add_argument('--enable-daily-private', action='store_true')
    args = parser.parse_args()
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        parser.exit(1, 'Install the optional google-auth-oauthlib package to authorize YouTube.\n')
    if args.output.exists():
        parser.error('output already exists. Choose a new private path.')
    try:
        document = json.loads(args.client.read_text(encoding='utf-8'))
        installed = document['installed']
        if (installed['auth_uri'] not in ('https://accounts.google.com/o/oauth2/auth',
                                          'https://accounts.google.com/o/oauth2/v2/auth')
            or installed['token_uri'] != TOKEN):
            raise ValueError()
        flow = InstalledAppFlow.from_client_config({'installed': installed}, SCOPES,
                                                    autogenerate_code_verifier=True)
    except (OSError, KeyError, TypeError, ValueError):
        parser.exit(1, 'Use the Desktop OAuth client JSON downloaded from Google Cloud Console.\n')
    try:
        credentials = flow.run_local_server(host='127.0.0.1', port=0, timeout_seconds=180, prompt='consent',
                                           success_message='Authorization received. Return to Shadow Studio setup.')
    except Exception:
        parser.exit(1, 'Google authorization did not finish. Check the selected account and OAuth consent settings.\n')
    try:
        write_config(args.output, credentials, args.channel_id, args.audience, args.enable_daily_private)
    except (OSError, ValueError, RuntimeError):
        parser.exit(1, 'Could not save authorization. Check the channel ID, granted scopes, and private output path.\n')
    print('Verified the existing channel and saved the worker credential file.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
