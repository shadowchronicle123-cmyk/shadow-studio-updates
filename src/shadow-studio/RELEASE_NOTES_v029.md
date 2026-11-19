# Shadow Studio v0.29 — Dual Voice, ElevenLabs Connect & Verified Updater

## New
- **Two-character narration:** Create > choose Main voice and Second character voice. The first speaker uses the main voice; other speakers use the secondary voice. Each scene renders with its assigned voice. The first version supports one main and one secondary voice, not unlimited per-character voices. Batch workflows still use their existing selected voice.
- **ElevenLabs connection:** Publishing > Integration health center > Connect ElevenLabs. Enter your own ElevenLabs API key. The app checks access through ElevenLabs' voice API before saving it to the local `.env`, then lists voices and lets you set the selected premium voice. Choose the premium voice from the main voice selector after reloading. ElevenLabs generation can incur charges on your ElevenLabs account, unlike the free voice engine.
- **Verified updater:** Publishing > Integration health center > Check for updates. There is also `update_windows.bat`, which fetches a remote release ZIP using a configured HTTPS manifest, verifies its SHA-256, backs up code, and preserves projects, videos, gameplay, clips, `.env` and `.venv`.

## To enable updates once
1. A trusted HTTPS server must host a release feed as JSON: `{"version":"0.30.0","url":"https://your-domain.example/releases/shadow-studio-v0.30.zip","sha256":"<64-character SHA256>"}`.
2. Add `SHADOW_UPDATE_FEED=https://your-domain.example/releases/latest.json` to your local `.env` (the ZIP URL must use the same hostname).
3. In Shadow Studio click **Check for updates**. When a release is available, stop the local server (`Ctrl+C` in its Command Prompt), double-click `update_windows.bat`, and restart normally. The updater downloads and installs for you without extracting a new ZIP manually.

**Important:** A working remote release feed is NOT included or hosted yet. Until one is configured, the app reports that automatic updates are unavailable. The first upgrade to v0.29 must still be downloaded/installed manually; do not claim this can update your existing v0.28 installation by itself.

## Tests executed
- Real 3-scene video with first/second/main voice assignment and SRT export (the voice audio in this automated test was synthetic; a real ElevenLabs render must be verified on your account).
- ElevenLabs connect with a mocked authenticated voice response and API-key persistence to a temporary `.env`.
- Update feed unconfigured check; corrupted/checksum-mismatched ZIP rejected.
- Valid simulated update installed with backups and verified preservation of `.env` and existing videos.
- UI presence and request validation; Python module compilation.

## Notes
- Never paste ElevenLabs API keys into ChatGPT or public comments. Enter keys only into your local Shadow Studio at `http://127.0.0.1:8765`.
- Updates modify app code; they do not update Python dependencies automatically. New requirements may require re-running `pip install -r requirements.txt`.
- For a two-person story, use exactly two consistent speaker names. A third speaker is currently assigned the secondary voice.
- No video has been uploaded or published.
