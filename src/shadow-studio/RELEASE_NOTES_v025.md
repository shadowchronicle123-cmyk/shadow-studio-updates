# Shadow Studio v0.25 — Prelaunch Reliability Audit

**Status: private local beta, NOT a public or multi-user deployment.**

This release builds on v0.24. It preserves the black-and-crimson interface and all existing video production tools.

## Changes

1. **Full encoded-video integrity verification:** the quality gate now decodes the complete video stream with FFmpeg and blocks videos containing damaged or undecodable frames. Checking only MP4 metadata was insufficient; corrupted streams can still expose a duration and track details.
2. **Fail-closed audio verification:** if a complete audio loudness scan cannot finish or fails, the video cannot receive technical approval. An audio stream being present does not guarantee usable narration.
3. **Local dashboard security:** the app rejects unsafe Host headers, blocks requests with foreign Origins, prevents clickjacking and applies browser MIME-type, referrer and cross-origin resource headers. Access remains localhost-only; do not bind to a public IP or forward a port to the internet.
4. **Text clipping protection:** message-story frames and gameplay-overlay captions choose as many recent bubbles as actually fit, keeping the newest dialogue visible. Very long text is shortened visually with an ellipsis; the complete words remain in the audio and SRT subtitle file. Long unbroken words now wrap within the panels.
5. **Built-in repeatable audit:** `prelaunch_check.py` tests the real video export pipeline, timed SRT, preapproval rejection, approved-only ChatGPT handoff, MP4 corruption blocking and local dashboard request protection, all with synthetic offline narration and temporary files. Added `httpx` as an explicitly installed dependency for these API tests.

## Testing performed in the build environment

- Offline story production: **PASS** 720 × 1280, 30 FPS MP4 with non-silent audio and two timed captions.
- Alternate Reddit-story and gameplay-overlay renderers: **PASS**.
- 1080 × 1920, 30 FPS render: **PASS**.
- Silent narrated WAV rejection: **PASS**.
- Damaged H.264 payload with still-readable MP4 metadata: **BLOCKED**, including after the sample video had previously been approved.
- Fake attacker Host/Origin requests and clickjacking headers: **BLOCKED / PRESENT**.
- ChatGPT handoff only after review, with original MP4 and SRT intact: **PASS**.
- Three-video starter batch and two-video idea batch with technical quality checks: **PASS**; video source fixtures are synthetic.
- Split-screen stacked and side-by-side, video ranking, caption editing, scene suggestions and auto-clipping: **PASS** with generated test media.
- Long-message layout and long unbroken words: **PASS** in frame-boundary tests.
- ZIP syntax/package verification: included in release audit.

## Not verified in this environment

- Actual ElevenLabs and Edge neural voice quality, quotas, credit use, or availability. The audio fixture is a **synthetic test signal**, not narration.
- Performance, installation, and protected workflows on your own Windows PC or actual iPhone.
- Real cloud-object-storage upload, private connector registration in your ChatGPT account, and actual vidIQ/YouTube publishing.
- Copyright/permission ownership of gameplay, images and music.
- Authentic user accounts or remote access; neither is ready.

**Approval inside Shadow Studio is not permission to post publicly.** You must separately review and approve the exact vidIQ publishing draft.

## Windows check (after installation)

```cmd
.venv\Scripts\python.exe prelaunch_check.py
```

If the command reports success, it verifies the **offline rendering and publishing handoff prerequisites**. It does not prove your chosen online neural voice works. Use the in-app voice preview before producing multiple Shorts.
