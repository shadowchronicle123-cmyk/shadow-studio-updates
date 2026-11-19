# Shadow Studio v0.28 — Approval Integrity & Voice Reliability

## Changes

1. **Exact-file approval:** On approval, the app stores a SHA-256 digest of `short.mp4`. If its bytes change afterward, the ChatGPT ZIP handoff, private cloud upload, MCP read-only connector, local draft staging, and approved publishing kit all reject it until you approve the updated file again.
2. **Publishing kit freshness:** The bulk approved upload kit now rechecks both exact-file approval and the current full technical quality gate. A file no longer qualifies only because `metadata.json` says approved.
3. **Race-safe voice previews:** Five simultaneous requests for an uncached voice now invoke the provider only once; the cached WAV is published atomically after basic WAV validation.
4. **Lower-memory hashing:** Streaming SHA-256 avoids loading an entire large video into RAM for cloud handoff and ZIP preparation.
5. **Regression test:** `prelaunch_check.py` verifies a technically playable MP4 modified after approval can no longer be exported until reapproved.

## Windows upgrade

Extract into a NEW folder and keep the old version as a backup. Copy your previous `outputs`, `projects`, `gameplay`, `uploads`, and private `.env` into the new inner `shadow-studio` folder only if you want to preserve those assets; do not share `.env`. Do not copy `.venv`; install dependencies in a new virtual environment as described in README.

**Important:** Videos approved in builds before v0.28 lack exact-file approval digests. They will remain in your library but require a new approval in the Review Queue before publishing-kit, cloud, MCP, or handoff access. This is intentional fail-closed behavior.

To run the offline checks (no voice credits / uploads):

```cmd
.venv\Scripts\python.exe integration_acceptance_v027.py
.venv\Scripts\python.exe integration_test.py
.venv\Scripts\python.exe production_smoke_test.py
```

## Tests performed in this development environment

- End-to-end synthetic-narration MP4 (720x1280, 30fps), audio and scene SRT: PASS
- Synthetic Reddit-style and gameplay-overlay exports: PASS
- Invalid and silent media rejection: PASS
- Local dashboard unsafe Host / cross-origin rejection: PASS
- Unapproved and edited-after-approval handoff rejection: PASS
- Deliberately damaged H.264 frame rejection: PASS
- MCP bearer-token read-only tool checks: PASS
- Simulated third-party integrations and secret redaction: PASS
- Five simultaneous voice-preview requests -> exactly one provider synthesis: PASS
- Code compilation and ZIP file integrity: PASS

**Unverified:** actual Edge neural voice sound and provider availability on your Windows PC; actual ElevenLabs API key or quota; R2/S3 live transfer, secure ChatGPT MCP connection (Plus support), and live vidIQ upload/draft from Shadow Studio. Nothing was published or charged. The separate vidIQ connection to your YouTube channel was confirmed in ChatGPT, not via this release.
