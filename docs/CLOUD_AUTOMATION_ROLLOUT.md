# Shadow Studio unattended Shorts rollout

## Objective
One original, technically verified Short per day, rendered on GitHub-hosted Linux, privately uploaded to the authorized ShadowChronicles YouTube channel. No PC required. **Not live.**

## Current blockers
- The existing Python `production_worker.py` needs owned/licensed background footage, original story premises, FFmpeg, narration, and a persistent SQLite ledger. GitHub-hosted runners are ephemeral: local ledger state is lost after each run.
- `private_upload.py` requires a securely stored Google refresh token, channel ID, OAuth client credentials and audience setting. Netlify's static site does not deliver these.
- `authorize_youtube_web.py` is a persistent-server OAuth implementation, not a deployable Netlify Function.
- YouTube resumable upload failures can be ambiguous. The worker intentionally stops and requires reconciliation instead of risking duplicate uploads.
- A cloud scheduler must not activate until secure credential transfer, durable ledger storage, stop controls, private-only verification and end-to-end tests are complete.

## Proposed low-cost architecture
1. Netlify: phone-friendly dashboard and OAuth entry point, **no public secrets**.
2. Durable secure service: one-time PKCE state and refresh-token handoff to worker, encrypted at rest with restricted access.
3. GitHub Actions: scheduled ephemeral video-render job using preapproved original premises and licensed media. Test usage and storage limits first.
4. Durable job ledger: unique day and video checksum; atomic claim/lock; reconcile uncertain uploads before retry. Never use a disposable GitHub runner SQLite file as the only record.
5. Protected worker credentials: secrets must be stored only in an approved secret manager / GitHub Actions secrets, never in artifacts, logs, Netlify static files, or repository commits.
6. Initially upload **private only**. Keep automatic public posting disabled until owner separately approves it.

## Release gates
- [x] Static Netlify status page in draft PR (not deployed)
- [x] Read-only cloud preflight workflow in draft PR (passing runs observed)
- [ ] Cloud render and mocked upload tests passing in GitHub Actions
- [ ] Durable ledger and secret handoff design reviewed and implemented
- [ ] HTTPS Google OAuth callback implemented, deployed and security-tested
- [ ] Owner completes Google consent for exact channel
- [ ] One verified private upload succeeds
- [ ] Daily scheduling explicitly approved and enabled

Do not assume that a passing mocked upload test confirms a live YouTube connection. Do not schedule unattended uploads or change privacy without explicit owner authorization.
