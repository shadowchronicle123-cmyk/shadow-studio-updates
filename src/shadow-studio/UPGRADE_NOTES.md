# Shadow Studio v0.15 — Crimson Edition

This release upgrades the existing v0.14 application in place. Keep your old ZIP and preserve your `gameplay/`, `outputs/`, `projects/`, `uploads/`, `.env`, and `advanced/` folders when moving to the new version.

## New, tested features

- Black, burgundy and crimson interface inspired by the supplied ShadowChronicles logo, with desktop and phone layouts.
- **Simple Editor**: vertical 720×1280 H.264 export, start/end trim, optional styled text overlay, and audio mute.
- **Auto Clip Suggestions + export**: uses FFmpeg scene-change analysis and can export up to three vertical clips. When no transitions exist, uses regular intervals. **It does not infer viral highlights from story or emotion**.
- **Image Motion**: creates a short, animated 9:16 MP4 from an uploaded photo or illustration. This is not a generative video model.
- Existing: text stories, gameplay backgrounds, ranked videos, split-screen videos, video library, review queue, story batches, natural voice selection.

## Optional features requiring setup

**Transcription** requires this additional package (inside the project's virtual environment):

```cmd
.venv\Scripts\python.exe -m pip install faster-whisper
```

The initial model may download weights, and the first transcription may be slower. The default CPU model is `tiny`; transcription quality varies by recording.

**AI image generation** is wired to an independently installed Stable Diffusion WebUI service running with API enabled at `http://127.0.0.1:7860`. The app posts a prompt to `/sdapi/v1/txt2img`. Its checkpoint, license, memory requirements and availability are outside this application's setup. The endpoint is fixed to localhost. The local API adapter has not been tested against a live GPU model in this environment.

**Neural narration** uses online voice services and may require network access, API keys, and paid credits. The system is set to report neural voice errors instead of silently replacing them with a robotic voice. Voice quality depends on the provider.

## Still not implemented

- AI semantic highlight selection (scene cuts only)
- Multi-track nonlinear timeline, word-level aligned subtitle editing
- Fully integrated AI video synthesis (image motion is not equivalent)
- Direct ChatGPT app connector/authentication
- Direct YouTube/TikTok publishing OAuth and scheduling
- Channel analytics integration

Use the existing review queue and approved upload kit for manually approved posting via YouTube or connected vidIQ.

## Install and run on Windows

1. Extract the new ZIP. Keep your older installation as a backup.
2. Open Command Prompt in the extracted `shadow-studio` folder.
3. Run `python -m venv .venv`.
4. Run `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
5. Run `.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8765`.
6. Open `http://127.0.0.1:8765` in your browser.

To avoid bypassing Smart App Control, you can run the Python commands manually; don't disable Windows security to run a downloaded batch file. Tailscale Serve can provide private phone access after separate configuration. Never expose this unauthenticated local API to the public internet.

## Privacy and permissions

This v0.15 version binds to `127.0.0.1` in its launch instructions. It does not implement account authentication. For access from an iPhone, first install a private network solution with access controls; Netlify Drop by itself cannot run the video-processing Python backend. Only import gameplay footage you own or are allowed to reuse.

## v0.16 — Export Quality Gate

- Review Queue has a **Run video quality check** button.
- Local `ffprobe` checks MP4 streams, vertical aspect ratio, width/height, frame rate, duration, and audio track.
- Local FFmpeg checks approximate audio peak and average loudness near the beginning of the clip.
- Videos missing an audio/video track or shorter than three seconds cannot be marked approved (HTTP 409). Other issues are warnings; the creator can still approve after reviewing.
- No media is uploaded for this technical check. The system **does not** verify natural-sounding voices, rights clearance, storytelling quality, or YouTube eligibility. Those require a human review.
- Private local endpoint: `GET /api/library/{12-char-video-id}/quality`.
- Never set the app's Uvicorn host to `0.0.0.0` without adding authentication and network controls. Localhost only is the default.
