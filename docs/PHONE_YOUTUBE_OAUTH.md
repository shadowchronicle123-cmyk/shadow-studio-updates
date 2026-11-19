# iPhone Safari authorization for private-only YouTube worker

This is an **opt-in, un-deployed** HTTPS Web application OAuth enrollment path.
The existing desktop authorization remains supported. No Google authorization,
production hosting, live private upload, or Windows end-to-end test is claimed.

## Prerequisites and exact callback

1. Provision an **owner-controlled HTTPS domain** on a persistent host, for
   example `https://oauth.example.com`. This is a placeholder, **not a deployed
   address**. The host can be the future worker host. Do not buy hosting without
   explicit approval. The public reverse proxy must terminate TLS and forward
   only `/oauth/start` and `/oauth/callback` to `127.0.0.1:8765`.
   Disable access logging for both routes (especially query strings), rate-limit
   the start route, and do not expose the backend port. No other endpoints are
   needed. The app intentionally ignores forwarded headers.
2. In Google Cloud Console select the owner project with YouTube Data API v3
   enabled. Configure the consent screen and add the Google account as a test
   user if in Testing. Create an **OAuth client ID → Web application**.
   Under **Authorized redirect URIs**, add exactly:
   `https://oauth.example.com/oauth/callback` (replace only the domain with
   your actual HTTPS domain). The URI must match the running server exactly.
   No JavaScript origins are needed for this server-side flow.
3. Download the Web client JSON **privately to the host**, not GitHub or chat.
   Place it outside the repository with owner-only permissions. Set the
   following environment variables in a private service environment file:
   `SHADOW_OAUTH_PUBLIC_ORIGIN=https://oauth.example.com` and
   `SHADOW_OAUTH_SETUP_KEY` to a randomly generated 48+ character value.
   Protect the environment file and ensure it is not written to logs.
4. On the host, install the existing Python requirements including `requests`.
   Start from `src/shadow-studio`:

   ```sh
   python authorize_youtube_web.py --client /private/web-client.json \
     --output /private/youtube.json --channel-id UC_YOUR_24_CHARACTER_CHANNEL_ID \
     --audience not-made-for-kids
   ```

   Set the audience to the owner's correct designation. The output must be a
   **new** path on private storage, with restricted parent-directory access.
5. Privately assemble `https://oauth.example.com/oauth/start?key=YOUR_SETUP_KEY`
   on the iPhone and open it in Safari. Do not share that URL in chat or GitHub.
   The link is single-use, expires after five minutes once opened, and Google
   redirects to the exact HTTPS callback. Google may require app verification
   or a test user; Testing-mode refresh tokens can expire quickly.
6. After consent, the server verifies `channels.list(mine=true)` matches the
   configured channel and writes `youtube.json` with mode 0600 and
   `SHADOW_YOUTUBE_PRIVATE_AUTOMATION=0`. Move the private file to the worker's
   protected credentials directory, then stop the enrollment server. Review a
   real export and run the existing manually confirmed private-only test before
   separately opting into daily automation.

## Security and limitations

- State and PKCE are random per attempt; the state is single-use and consumed
  before token exchange. The token exchange occurs only on the trusted host.
  Callback errors do not echo codes, tokens, or Google error descriptions.
- The setup URL contains a bearer setup key. **Reverse-proxy access logs,
  analytics, browser history, and shared screenshots can leak it.** Disable
  logging for enrollment routes, avoid analytics, keep the URL private, and
  rotate the setup key after use. A compromised setup key can initiate the
  one permitted enrollment, but not read stored worker credentials.
- The server is a minimal localhost HTTP backend, **not an HTTPS server**.
  Production requires a correctly configured trusted TLS reverse proxy,
  firewall, DNS, certificate, and persistent private worker storage.
- OAuth consent does not itself enable uploading. Live Google consent, refresh
  renewal, real private upload, and end-to-end phone testing remain pending.
  Public publishing is not supported by this enrollment flow.
