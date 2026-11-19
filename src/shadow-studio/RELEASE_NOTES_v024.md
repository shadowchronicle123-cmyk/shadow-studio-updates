# Shadow Studio v0.24 — QA Hardening

This is a local Windows private beta, based on v0.23. No YouTube publishing or ChatGPT account connection has been activated.

## Fixes
- The quality gate checks the **complete audio track**, not only the opening 12 seconds. An MP4 with an encoded but effectively silent AAC track is blocked before approval or handoff.
- Narration generation rejects empty scenes and malformed stories before running FFmpeg.
- Empty story ideas return a controlled input error instead of an unhandled server error.
- Quality reports now include `overall_max_volume_db` in addition to opening loudness diagnostics.

## Regression testing (offline in this environment)
- Real 720x1280 and 1080x1920 30fps story exports with audio and timestamped subtitle files: PASS.
- Actual Reddit style and gameplay-overlay MP4 exports: PASS.
- Silent generated WAV rejected: PASS.
- Encoded silent AAC MP4 blocked and later-audible clip accepted: PASS.
- Empty/invalid story inputs rejected: PASS.
- Split screen stacked and side-by-side, video ranking, caption editor, scene suggestions and auto-clipping: PASS.
- Three-story and custom batch flow with quality checks: PASS (real FFmpeg fixture files; simulated voice generation).
- Approval/review queue, subtitle downloads, and approved-only handoff ZIP with explicit publish permission restriction: PASS.
- MCP owner token, approved-only listing, invalid token and OAuth rejection: PASS (local tests; live ChatGPT connection NOT tested).
- Chromium desktop browser generation and tab navigation without JavaScript errors: PASS.

## Important limits
- Online neural/ElevenLabs voice output and billing/quota status were NOT tested here. Offline synthetic voice validates the encoding pipeline, not the sound quality or narration expressiveness.
- Cloud storage upload, iPhone device behavior, Windows runtime, ChatGPT connector login and vidIQ publishing were NOT live-tested.
- The full-audio checker verifies audio amplitude, not speech correctness or copyrighted material.
- Local dashboard is not multi-user authenticated. Keep it bound to localhost until proper accounts and secure hosting are implemented.
- Owner approval in the library is NOT permission to publish publicly. Publication requires a separate explicit approval.

Start with `start_windows.bat` (or the commands in README.md). Run `production_smoke_test.py` to verify your own FFmpeg installation without spending voice credits.
