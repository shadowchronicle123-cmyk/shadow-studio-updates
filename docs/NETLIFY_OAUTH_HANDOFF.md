# Netlify OAuth hosting handoff — not yet live

This branch adds a safe static Netlify landing page to eliminate the misleading 404. **It does not implement OAuth, store tokens, or upload videos.**

## Deployment

- Existing Netlify site: `https://shadow-studio-updates.netlify.app` (owner-supplied, not independently verified).
- Existing production branch: `feature/iphone-web-oauth-private`.
- Proposed changes are isolated on `feature/netlify-oauth-safe-landing` and require an intentional branch/deployment change after review.
- Netlify uses `netlify.toml` to publish `netlify-site/` with no build command.
- Do not add OAuth client secrets or refresh tokens to the static site, build output, or public Git repository.
- Do not register `https://shadow-studio-updates.netlify.app/oauth/callback` with Google yet: this branch intentionally does not accept callbacks.

## Remaining prerequisites for a working iPhone OAuth flow

1. Choose a **durable and secure worker-side token store** and authenticated one-time transfer mechanism. Netlify's static site cannot safely store a refresh token for the Python upload worker.
2. Implement serverless OAuth start/callback with PKCE, short-lived single-use state in durable storage, strict redirect URI checks, no token disclosure, no access-log leakage, replay protection, and explicit YouTube channel verification.
3. Test authorization errors, expiry, replay, token exchange, persistence, transfer, refresh, and worker integration offline. Review deployment permissions and rate limits.
4. Only after successful HTTPS deployment, register the **exact** callback URI in Google Cloud, then conduct a live Safari authorization and manually approved **private-only** upload.
5. Keep `SHADOW_YOUTUBE_PRIVATE_AUTOMATION=0` until explicit owner approval.

The existing Python OAuth server in PR #11 is designed for a persistent trusted host and cannot simply be run as a Netlify Function.
