# Shadow Studio v0.26 — Integration Readiness Edition

Built on the v0.25 prelaunch-audited package. All previous production and review features remain in the package.

## Added

- Integration Health Center on the Publishing tab, using the same dark/crimson design.
- `GET /api/integrations` to inspect configuration without any provider requests.
- `POST /api/integrations/check` for owner-triggered, **read-only** checks. Does not generate a paid voice, upload a video, publish anything, or show API keys.
- Diagnostics for Ollama model availability, Edge neural voice dependency, ElevenLabs account/voice access, local Stable Diffusion image API, faster-whisper installation, private S3/R2 bucket, and private MCP owner setup.
- Explicit status for vidIQ: publishing remains a separate authorized ChatGPT workflow, not a direct local-app API integration.
- Offline integration tests covering mock provider successes/failures, secret redaction and cross-site/Host security restrictions.

## Running on Windows

Use the existing v0.25 Windows installation procedure. Copy your real `.env` from your previous installation into this new extracted project if you used external providers; the ZIP includes only `.env.example`, never actual keys.

1. Install dependencies: `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
2. Start: `.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8765`.
3. Open `http://127.0.0.1:8765`, select **Publishing**, find **Integration health center**, and click **Test provider connections**.
4. Preview the actual selected neural voice before using credits for an ElevenLabs batch.
5. Run `.venv\Scripts\python.exe prelaunch_check.py` before creating Shorts.

Only the Windows computer running the server can access `127.0.0.1`; exposing it on the network requires separate authentication and hardening.

## Verification

- Compilation of app and diagnostics: PASS.
- Real FFmpeg vertical render, caption export, quality checks, review restrictions and corruption rejection (`prelaunch_check.py`): PASS.
- Simulated provider responses, missing model/voice, secrets not echoed, cross-site/Host protection (`integration_test.py`): PASS.
- Browser desktop/mobile Publishing page: PASS, no JavaScript errors or horizontal overflow.
- Actual online voice synthesis, live account credentials, private storage upload, production MCP OAuth and vidIQ publishing: **NOT TESTED**.

## Explicit limitations

This release **does not** automatically open a message in this ChatGPT conversation, connect to vidIQ from the Windows app, or publish to YouTube. These require platform/account authorization and a user-initiated ChatGPT request. A configured key or successful read-only API probe is not proof that paid generation/export works. Do not treat this as a production-ready public platform.
