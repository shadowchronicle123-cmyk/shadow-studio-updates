# Shadow Studio v0.22 — Publishing-night reliability

This release improves the existing local Windows private beta; it does **not** establish a live ChatGPT connection, publish to YouTube, or verify online voice credentials.

## Changes

- Every completed story render creates a timestamped `captions.srt` file aligned to the **scene narration timeline**. Cues are not word-by-word transcription. Available from the Review Library as **Download captions (.srt)**.
- All single-video and batch story jobs now run their technical MP4 quality inspection **before** the video is archived into the completed library. A corrupt, silent-track-free, or too-short export does not appear as a successful finished Short.
- Approved handoff ZIPs and the bulk publishing kit include `captions.srt` when generated, preserving optional editable captions for later finishing.
- A new automated integration test validates a real two-scene 720×1280 MP4, synchronized SRT timings, readiness, approval logic, and export contents using synthetic fixture audio. No premium speech credits were consumed.

## Honest limitations

- Captions are sentence/scene-timed and are not burned as word-by-word subtitles. The existing scene graphics are part of the MP4.
- Synthetic audio used in the automated test proves encoding, not natural speech quality. The real selected AI voice must still be previewed and heard on your computer.
- Cloud uploads need private storage credentials. vidIQ publishing remains an explicitly approved separate step. No direct injection into an existing ChatGPT conversation is available.
- Tests here are on a Linux build runner, not your Windows machine. Run `self_test.py` and render a real voice sample locally before posting.
