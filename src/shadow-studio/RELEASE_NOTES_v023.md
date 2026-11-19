# Shadow Studio v0.23 — Crimson Video and Narration QA

## Changes since v0.22

- Updated the **actual exported story videos** from the old blue palette to the ShadowChronicles black/crimson theme. Both message and gameplay-backed layouts use coordinated colors.
- Displays the story title prominently on early scenes with sufficient open space; avoids overlaying crowded conversations.
- Verifies each rendered narration scene **after** normalization to 16-bit mono WAV, rejecting empty/very short/effectively silent samples before spending time on the final MP4 render. The error appears in the job status rather than marking the video complete.
- Added `production_smoke_test.py`: creates real FFmpeg MP4 exports in a temporary folder for message, Reddit and gameplay-backed formats, verifies non-silent audio, timings and subtitles, and tests silent-voice rejection. This test is free and uses a clearly labeled synthetic stand-in tone, not a provider's natural voice.

## Local Windows verification

1. Keep v0.22 as a backup. Extract v0.23 into a **new folder**; do not overwrite a running installation.
2. From the `shadow-studio` folder, use the installed Python environment (`.venv\\Scripts\\python.exe` or `python` after dependencies are installed):
   ```bat
   .venv\\Scripts\\python.exe production_smoke_test.py
   ```
3. Start the application with your normal command and use **Voice Preview** with the voice you intend to render. A real Edge/ElevenLabs voice check requires internet and, for ElevenLabs, your own valid account and quota.
4. Render one real Short, **listen to the entire narration** and review the video, subtitle timing, and rights before approval.
5. Send approved Shorts into the established ChatGPT/vidIQ draft workflow. Do not treat video approval as YouTube publishing approval.

## Tested here

- Real 720x1280 at 30 FPS MP4 exports with audio for message, Reddit and gameplay-backed layouts
- Timed SRT subtitle generation and technical quality analysis
- Correct failure on silent scene audio
- Local API startup and production readiness routes

## Not verified

- Live Edge or ElevenLabs narration and naturalness (no provider access from this test environment)
- This exact ZIP on your Windows device, codec availability on your computer, or your connected YouTube/vidIQ account
- Automated delivery into this ChatGPT conversation or automated public posting

**This is an incremental local beta, not a claim of full Viblo parity.**
