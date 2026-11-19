# Shadow Studio v0.28 — Crimson Private Beta

**Latest update:** Exact-file approval checks, bulk publishing-kit safety, and preview concurrency protection. See [RELEASE_NOTES_v028.md](RELEASE_NOTES_v028.md).

**Before upgrading:** Existing approved videos will need a fresh approval under v0.28 before handoff. This app is local-only. It is not directly linked to this ChatGPT conversation, and publishing still requires your explicit final authorization through vidIQ.

---

# Shadow Studio v0.25 — Prelaunch Reliability Edition

Read [RELEASE_NOTES_v025.md](RELEASE_NOTES_v025.md) for the changes and scope of verification. **This version is local-only and not ready for public deployment.**

After extracting and installing dependencies, run `.venv\Scripts\python.exe prelaunch_check.py` for an offline, non-destructive, production-quality test. This uses no paid voice credits and does not upload or publish anything.

---

> **Latest:** v0.24 QA hardening. See `RELEASE_NOTES_v024.md` for verified tests and outstanding live checks.

# Shadow Studio v0.21 — Crimson Pro (Private Beta)

This build is **a local, single-owner beta** for creating and approving YouTube Shorts. It is not a hosted multi-user platform and does not publish to YouTube on its own. The interface is a polished responsive Crimson Edition built around your ShadowChronicles branding, not a copy of Viblo's UI.

## First run tonight (Windows)

1. Make sure your existing Python 3.12 and FFmpeg are installed; `ffmpeg -version` should work in Command Prompt.
2. Unzip this release, open the inner `shadow-studio` directory, then open Command Prompt at that location.
3. Run `py -3 -m venv .venv` and `.venv\Scripts\python.exe -m pip install -r requirements.txt` (only needed on first install).
4. Run `.venv\Scripts\python.exe self_test.py` to check the local setup without spending narration credits.
5. Run `.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8765` and browse to `http://127.0.0.1:8765` on that PC.
6. On Create, check the production status, **preview an actual voice** (this verifies external voices), select a ready story, render a 720p Short, review it in Library, and approve only after a full listen/watch. Use the handoff link/package for the final ChatGPT → vidIQ draft. **Your separate approval is always required to publish.**

If you are upgrading from a prior version, keep a backup and copy your existing `outputs`, `projects`, `gameplay`, `uploads`, and local `.env` into this new directory. Keep `.env` private. Do **not** expose port 8765 publicly; there is no multi-user login.

## Tested for v0.21

- Main FastAPI routes, launch-readiness counts, starter stories, voice catalog, file downloads, and error responses.
- Real synthetic-fixture render exported 720×1280 at 30 FPS with audio. This checks the encoding/mux pipeline, **not natural narration quality**.
- Real MP4 exports from split-screen (both layouts), video ranking, simple editor with caption, and auto clipper; scene detection fallback.
- Browser story creation and tab navigation; mobile (390px) and desktop (1512px) layouts with no horizontal overflow or JavaScript errors.
- **Not tested live:** neural voices, ElevenLabs paid voices, Windows host environment, private cloud storage, custom ChatGPT MCP connection, or vidIQ posting. They require the appropriate installations, keys, permissions, and approvals.

See `RELEASE_NOTES_v021.md` and `TONIGHT_CHECKLIST.md` for details.

---

# Current update: v0.15 Crimson Edition

Read [UPGRADE_NOTES.md](UPGRADE_NOTES.md) for the working additions, limitations, and Windows installation steps.

# Shadow Studio v0.9 — Private Windows + iPhone review beta

Shadow Studio is a local production tool for original Shorts. It is **not** yet a full Viblo replacement. New in v0.9: persistent gameplay library with automatic least-used clip rotation and split-screen gameplay/story visuals. Natural neural voices and optional ElevenLabs narration remain included, along with mobile-friendly layout and private phone access guide. The Video Library retains completed exports across restarts.

## What's included
- Editable short stories with text-message or Reddit-style scenes. The offline script is a generic template; optional Ollama creates original scripts.
- Eight selectable online neural voices (plus an optional ElevenLabs premium voice) with previews and voice rates; Windows voice available as offline fallback. **Commercial usage terms for voices must be verified independently** and online availability is not guaranteed.
- 720×1280 or 1080×1920 MP4 exports at 30 FPS. Artwork originates at 540×960 and may be upscaled.
- **Gameplay Library**: upload owned/licensed gameplay once, categorize it, select saved clips, or choose Automatic rotation for regular and batch jobs. The gameplay occupies the top 40% of the Short, with story text below. Usage counts and a clip start cursor are saved in `gameplay/library.json`. Uploaded gameplay is capped at 500 MB per clip. The original one-time upload still works.
- Save and reload projects; batch-render 1–8 videos, sequentially; basic manual trim/crop.
- **Video Library**: completed videos appear in the dashboard after restart, can be replayed, downloaded, and their descriptions copied. Saved video metadata is in `outputs/<id>/metadata.json`.
- YouTube titles and descriptions for review. **No automatic publishing or YouTube account connection** in this beta.

## Install/upgrade from v0.6 (Windows)
Your Python 3.12 and FFmpeg installations can stay as they are.

1. Stop the old server in Command Prompt with `Ctrl+C`.
2. Extract `shadow-studio-v0.9.zip`. Open the inner `shadow-studio` folder containing `app.py` and `requirements.txt`.
3. Click the File Explorer address bar, type `cmd`, and press Enter. Verify the prompt ends with `shadow-studio>`.
4. Enter each command separately:

```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8765
```

5. Browse to `http://127.0.0.1:8765` on the same PC. Leave Command Prompt open while you use the dashboard.
6. Select a voice and use **Preview voice**, generate/edit a story, and render at 720p first. Scroll to the **Video Library** to reopen completed renders.

To preserve videos/projects from your previous version, **copy the `outputs`, `gameplay`, and `projects` folders from the old inner `shadow-studio` folder to the new one before launching**. Do not overwrite newer files. If you have an `uploads` folder, copy that too to preserve your local background footage. Your previous version remains a backup.

Do not disable Windows Smart App Control or bypass any blocked program. If a command is blocked, inspect the warning. The manual commands above avoid the BAT launcher.


## Optional premium narration — ElevenLabs
The eight built-in online neural voices still work without a paid account, subject to service availability and terms. **The premium integration is optional and may incur a charge for every preview and rendered story line.** No ElevenLabs key is bundled or shared.

1. Create an ElevenLabs account and obtain an API key and the voice ID of a voice you are permitted to use. Check plan usage, commercial rights, and remaining credits.
2. Make a copy of `.env.example` named `.env` in the inner `shadow-studio` folder (Windows: turn on file extensions in File Explorer).
3. Add `ELEVENLABS_API_KEY=your_key` and `ELEVENLABS_VOICE_ID=your_voice_id` to `.env`. Keep this file private and **never send it into chat or upload it to Netlify or GitHub**.
4. Restart Shadow Studio. The selected premium voice appears in the narrator dropdown. Press Preview to compare. Each preview uses paid API allowance.
5. Do not choose the Windows default voice if you want natural narration. On any neural voice error, rendering stops without a robotic fallback.

The premium narration model is `eleven_multilingual_v2`; it is a compatibility-oriented choice and might not be the newest or best model. Voice speed settings currently affect the built-in online voices, **not ElevenLabs**. The local app never automatically posts videos.

## Optional Ollama AI scripts
Install Ollama from https://ollama.com/download/windows, run `ollama pull llama3.2:3b`, and create `.env` from `.env.example` with `SHADOW_OLLAMA_MODEL=llama3.2:3b`. The model runs locally; actual performance depends on your NVIDIA GPU. If Ollama is missing, the app falls back to a generic template.

## What changed and validation performed
- Added persistent gameplay upload/list/selection API routes, plus rotation across batch renders.
- Tested upload with a 10-second test-pattern MP4, generated a vertical 720×1280 / 30 FPS split-screen MP4 with fake test narration, and checked frame output and usage counter. **The supplied visual test is a generated test pattern, not real Minecraft footage or a demonstration of natural speech.**
- Verified updated dashboard HTML and JavaScript syntax.
- Neural voices and premium ElevenLabs still require a Windows/network/provider test.

## Previous validation performed
- App loads with FastAPI test client and health endpoint responds.
- Exported a 14-second original 720×1280 / 30 FPS MP4 with an audio track in the test environment.
- Verified library metadata persists on disk and the list, detail, and video-download API endpoints work.
- The sample MP4 uses the **test environment's basic offline speech**. It is not a demo of Windows or neural voice quality. Voice options and Windows setup still require validation on your PC.

## Limitations / security
The app binds to `127.0.0.1` only. It has no login or public remote access. Do not expose it over the internet or port-forward it. Rendering is single-job at a time. The server must remain running during generation. Only use owned/licensed media, review all scripts and videos before publishing, and check third-party speech-service terms. No automatic YouTube upload, analytics, polished timeline editor, cinematic video generator, or autonomous ChatGPT app control in v0.9.

## Private iPhone access (optional, after testing on Windows)

**Do not upload the Python app to Netlify Drop** or expose port 8765 to the public internet. Netlify Drop only serves a static frontend; this app requires Python and FFmpeg on your PC. A future frontend can use Netlify with a separately secured backend, but this release keeps everything on your PC.

For private remote control, install the official Tailscale app on your **Windows PC** and **iPhone** and sign in to the same tailnet. While Shadow Studio is running on your PC at `http://127.0.0.1:8765`, open a **second** Command Prompt on the PC and run:

```cmd
tailscale serve --bg 8765
tailscale serve status
```

Tailscale will display an HTTPS address (often ending in `.ts.net`). Open **that exact address** on your iPhone with Tailscale enabled. If your computer does not recognize `tailscale`, install Tailscale from https://tailscale.com/download. Keep your PC on and the Shadow Studio server running. Do not enable **Tailscale Funnel**, port forwarding, or a public Netlify proxy; they would change the security model. The app has no separate password, so limit your tailnet membership to trusted devices and use Tailnet access policies if you add other users. If a URL is not displayed or Tailscale requires configuration, follow its prompts before trying remote access.

To remove the share later, run `tailscale serve --reset` on the PC. The Windows dashboard itself remains bound to `127.0.0.1`, not your public IP.

### Voice quality policy
Select an online neural narrator and use **Preview voice** first. **Natural voices are the default**; the offline Windows voice is available only if you explicitly choose it. Failure of online voice generation stops the render with a descriptive error instead of silently substituting robotic speech. Natural voice quality and commercial permissions should be checked before monetization; this is not ElevenLabs.


## v0.10: simplified layout and ChatGPT handoff

The dashboard now has four navigation tabs: Create, Library, Automation, and AI Assistant & Publish. Voice, gameplay selection, story generation, and rendering remain together in Create.

From Library, open any rendered video to populate the AI Assistant & Publish tab with its title and description. The **Copy request for ChatGPT** button copies a text prompt; you must paste it into ChatGPT and attach the exported MP4 yourself. This is a human-operated handoff, **not** a direct ChatGPT API/MCP integration and **not** an automatic YouTube uploader.

A future direct connection requires a secure authenticated bridge, user account linking, YouTube OAuth scopes, and explicit per-video publishing confirmation. Never expose the local app unauthenticated to the public internet.

## v0.11 Tonight's Shorts workflow — new

This version adds three **hand-written, original, editable** story scripts to Create (buttons `Load story 1`, `Load story 2`, `Load story 3`). They have narrative twists rather than the old generic fallback template. You can render each separately, or use the Automation tab for up to eight *ideas* (note: the batch generator uses the basic offline story template unless you configure Ollama; for high-quality stories tonight, use the three complete curated story buttons individually).

### Production order for tonight
1. Install this new version in a separate folder; copy the old `outputs`, `projects`, `gameplay`, and `uploads` folders to preserve your work. Keep your existing v0.10 installation unchanged as a backup.
2. Start the local server with Python and FFmpeg as described above. The dashboard is `http://127.0.0.1:8765`.
3. In **Create**, choose `Load story 1`, inspect the actual lines, select a neural narrator, and **Preview voice**. If the natural voice fails, fix connectivity rather than switching to robotic voice; no silent fallback occurs.
4. Select **Automatic — rotate saved clips** from your own licensed Gameplay Library. Choose **720p** for the first export, then 1080p only if speed and quality suit you.
5. Render the MP4. Repeat for stories 2 and 3, modifying the dialogue for your channel as needed.
6. In **Library**, watch each complete video, choose its review decision, and click **Save review decision**. Only mark it **Approved** after checking sound, captions, rights, and story quality.
7. Click **Download approved upload kit**. The ZIP includes each approved MP4, its YouTube title/description in `youtube.txt`, plus an upload checklist. **Approval does not publish, upload, or share your videos.**
8. Upload to YouTube Studio yourself, or attach the approved MP4 in ChatGPT and request a vidIQ publishing draft. Inspect and separately approve the public post. YouTube may limit or reject uploads due to account and platform policies.

### Review Queue API
- `GET /api/library` lists finished videos with `review_status` and `review_notes` fields.
- `PATCH /api/library/{id}/review` accepts JSON `{ "status": "approved", "notes": "..." }` (or `needs_review`, `needs_changes`).
- `GET /api/publishing-kit` downloads a ZIP **only** of approved completed videos; error 404 if none are approved.

### Current limitations
- Direct YouTube account linking and autonomous publishing are **not available** in this version. vidIQ publishing remains a separate ChatGPT workflow requiring user review and approval.
- Neural voice generation requires internet, and ElevenLabs needs the user's valid API key and available credits. No provider account is bundled.
- Rendering three videos can take time on Windows. This release is a testable beta, not a completed Viblo feature-for-feature replica.
- Local web dashboard has no own password; keep the server on localhost, or use a trusted private Tailscale connection only.

## v0.12: One-click curated Shorts batch

The **Automation** tab now has **Render all 3 ready stories**. Unlike the ordinary idea-to-video batch, this uses the three complete original scripts from the Create tab unchanged. First select a narrator, test it with **Preview voice**, select a gameplay library clip or **Automatic**, and choose 720p for the first run. Then go to Automation and click **Render all 3 ready stories**. Video generation runs sequentially; keep the PC and terminal running. Each completed video appears in **Library**. Review the MP4 and rights to gameplay/voice, mark it **Approved for upload kit** if satisfied, then select **Download approved upload kit**. This generates videos and metadata only; it does not upload to YouTube or post publicly.

For errors, keep the terminal open, and read the batch status. Natural neural voices require online access. This release does not provide a direct authenticated ChatGPT or YouTube publishing connection. The offline Windows voice is available for troubleshooting but is not suitable as a polished narrator.

### v0.12 test scope

Automated tests exercised app startup, the starter-story endpoint, the curated batch queue (with synthetic render stand-ins), review state updates, and the approved ZIP's files. A separate FFmpeg test rendered a 720 x 1280 MP4 at 30 FPS with synthetic test sound; online neural voice quality and full three-video Windows rendering remain unverified and must be tested by the user. No gameplay clips from third parties are bundled.


## v0.13 — Split Screen Studio

The **Video Tools** tab now includes an actual FFmpeg split-screen editor. Upload any two MP4/MOV/WebM/MKV videos, choose top/bottom or side-by-side, select 3–120 seconds, and download a 720×1280 30 FPS MP4. Both clips loop if shorter than the requested duration. The tool mutes the original audio to avoid accidentally including unlicensed music. All processing stays on the local Windows server and nothing is posted.

This is a basic split-screen composer, **not yet** a full multi-track timeline, ranking editor, or automatic highlight detector. Only upload footage you own or have commercial reuse rights for.

## v0.14 — Video Ranking Studio

Open **Video Tools → Video Ranking Studio**. Select 2–5 gameplay videos you recorded or have permission to reuse, name the clips in ranking order (#1 is best), choose 3–8 seconds per entry, and click **Create ranked Short**. The completed video is 720×1280 at 30 FPS with a countdown from the lowest rank to #1. It mutes the original footage and does not automatically add music or narration. Download and review the MP4 before publishing. The UI sends clips to your private local Python server and stores temporary uploads under `uploads/`; the resulting MP4 is saved under `clips/`. A maximum of 200 MB per upload applies. No internet account or AI credits are required for this tool.

**Testing:** Local automated checks covered dashboard startup, invalid upload rejection, two-clip upload, ranking export, and playable MP4 download. Tests used synthetic footage, not licensed gameplay. The Windows end-to-end workflow still needs user testing.

### v0.16 video quality gate

Open **Library**, select a completed Short, and choose **Run video quality check**. The report includes dimensions, FPS, duration, audio presence, opening loudness, blockers, and warnings. Missing audio and very short exports are blocked from **Approved** status. You must still listen to every video and confirm you have rights to the gameplay and narration. Approval is local; it does **not** publish anything.

## v0.17 — Secure integration foundation

- The new **AI Assistant & Publish** tab stages local YouTube metadata drafts after you review and approve a video in Library. It does **not** upload anything.
- The optional owner-only endpoints under `/api/agent/` require an `X-Shadow-Agent-Token` header with a 32+-character secret configured as `SHADOW_AGENT_TOKEN` in your local `.env`. To generate a secret, run `python -c "import secrets; print(secrets.token_urlsafe(36))"` on your PC. Do not put the token in chat, screenshots, browsers, or public websites.
- Endpoint examples: `GET /api/agent/status`, `GET /api/agent/videos`, `GET /api/agent/drafts`, `POST /api/agent/drafts` (JSON: video_id, title, description, privacy). Draft creation requires the original video to be approved. API access is disabled until you configure the secret.
- This is **not yet a connected ChatGPT plugin**, nor a YouTube uploader. It is a private interface that can later back an authenticated integration. Do not port-forward your local server to the public Internet. On phones, use a restricted private network such as Tailscale.
- The interface's **Save publishing draft** button creates a local metadata-only draft; it never schedules or publishes to YouTube. Use YouTube Studio or the connected vidIQ service for the actual upload, with explicit approval before making a video public.

## v0.19: Optional automatic approved-video handoff to ChatGPT + vidIQ

Shadow Studio can transfer an approved, quality-checked MP4 to **your own private S3-compatible storage** (AWS S3 or Cloudflare R2), and return a signed 24-hour HTTPS download link plus a ready-to-paste ChatGPT request. The file is not placed in public storage. Whoever has the signed link can access it until expiry, so do not share the link outside the intended publishing chat. Uploaded objects remain in your bucket until removed; configure bucket lifecycle cleanup.

1. Install updated dependencies (`.venv\\Scripts\\python.exe -m pip install -r requirements.txt`).
2. Create a **private** object-storage bucket and credentials scoped to that bucket. Copy `.env.example` to `.env` if you do not already have one. Set `SHADOW_S3_BUCKET`, `SHADOW_S3_REGION`, `SHADOW_S3_ACCESS_KEY`, `SHADOW_S3_SECRET_KEY`, and optional `SHADOW_S3_ENDPOINT` (use a secure HTTPS URL for R2). Keep `.env` on your PC.
3. In **Library**, review the Short and approve it. Click **Generate private video link** and copy the ChatGPT + vidIQ request shown. Paste it into this ChatGPT conversation. ChatGPT can then ask vidIQ to import the link and stage a publishing draft, where supported.
4. Set `SHADOW_HANDOFF_AUTO_UPLOAD=true` in `.env` and restart to automatically transfer the MP4 *after approving it*; the share link can still be generated on demand by pressing the button. Upload does not publish or ask ChatGPT to do anything automatically.

**Important:** ChatGPT cannot automatically initiate itself from this offline Windows program. This version removes the manual ZIP/MP4 attachment, but still requires a user to paste/submit the request in ChatGPT and later explicitly approve the exact YouTube draft. A true zero-click bridge would need an authorized hosted integration. S3-compatible storage must be configured and independently tested with your bucket; the package has been tested with a simulated cloud service, not your real credentials.

## v0.20 — Private ChatGPT MCP connector

The optional **separate** `shadow_mcp.py` server exposes only approved, technically valid video metadata and temporary S3 links. See `RELEASE_NOTES_v020.md` for setup, OAuth/tunnel requirements, and limitations. This does not send unsolicited messages into an existing ChatGPT conversation and cannot publish to YouTube. Never expose the owner dashboard on port 8765 through the tunnel.

## v0.22: caption export and verified batches

Every new story render produces `captions.srt` containing timed scene-level dialogue cues, in addition to `short.mp4`. Download either from the completed preview or select a video in **Review Library**. Approved ChatGPT/vidIQ handoff ZIPs include both files when available. Captions are **scene-timed**, not word-synchronized speech recognition.

Finished single and batch renders are now checked with FFprobe before being marked complete. Export failures stay out of the review queue. This does not verify actual online neural voice quality; run the **Voice Preview** control with your selected voice and listen to the whole Short before uploading.

When upgrading an existing Windows installation, keep your own `outputs/`, `gameplay/`, `projects/`, and `.env` settings. Unpack the new version into a fresh directory, then copy those personal folders/settings only if needed. Never expose `.env` or cloud credentials.

## v0.27 voice and integration acceptance

See `RELEASE_NOTES_v027.md`. Run `.venv\Scripts\python.exe integration_acceptance_v027.py` on Windows to verify the local MCP bridge, narration preview caching, video export, captions and publishing restrictions. This uses only synthetic audio and **does not prove an online voice works**.

The Matilda voice appears as a selectable ElevenLabs premium voice *only if you separately configure a valid ElevenLabs API key* in `.env`. Being connected to vidIQ does not automatically grant that API key. The application caches each successful voice preview to avoid repeating the same paid synthesis request.
