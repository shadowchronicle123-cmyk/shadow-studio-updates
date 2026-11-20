# Offline, rights-gated streamer highlight suggestions

This is a **draft** extension of the existing `streamer_clips.py` rights ledger and `studio_tools.clip_media` FFmpeg renderer. It does not download from YouTube/Twitch, invoke cloud AI, upload media or publish to YouTube. It does not modify the three existing private N3on R3born videos.

## Workflow

1. Register owner-owned or documented edit-and-repost-licensed source footage with the existing `streamer_clips.py` manifest and SQLite ledger. The source must be available as an actual local file; a public URL is **not** permission.
2. Run `highlight_suggestions.py` for the registered clip ID. It rehashes the approved source bytes, checks the editing license and examines at most 180 seconds of its decoded audio. The deterministic heuristic ranks up to three non-duplicate 15–50 second windows by RMS energy, dynamics and silence.
3. Review suggestions carefully. Audio activity only indicates louder/less-silent segments; it does **not** identify speech, laughter, drama or actually engaging moments. No title or transcript is fabricated.
4. Render any candidate to an actual 1080×1920, 30 FPS local MP4. It uses the existing FFmpeg clip tool, retains the source audio content with loudness normalization, runs the existing audio/video quality check and refuses to overwrite an existing preview. Both the output and its SHA-256 are returned; it remains **review_required**.
5. The owner still must inspect the full result, captions, context, rights, audio and truthful metadata. The current command does not approve or upload this result automatically.

## Windows PowerShell

```powershell
cd "$HOME\shadow-studio-updates"
git fetch origin
git switch feature/rights-gated-audio-highlights
python -m pip install -r src\shadow-studio\requirements.txt
cd src\shadow-studio
python streamer_clips.py --manifest 'C:\ShadowStudio\clips.json' --media-root 'C:\ShadowStudio\licensed-footage' --database 'C:\ShadowStudio\clips.sqlite3'
python highlight_suggestions.py --database 'C:\ShadowStudio\clips.sqlite3' --clip-id 1 --max-results 3 --output 'C:\ShadowStudio\suggestions.json'
python highlight_suggestions.py --database 'C:\ShadowStudio\clips.sqlite3' --suggestion-id 1 --output-dir 'C:\ShadowStudio\clip-previews'
python highlight_suggestions_test.py
```

Replace the paths and IDs with those returned by registration and suggestion generation. Keep the source license evidence and written permission separately. Do not combine this source ledger with unrelated daily story queues without a deliberate data migration.

## Limits and remaining work

- There is no speech-to-text or speaker identification here; those require an installed offline transcription engine and a separate confidence/accuracy review.
- Source-license expiration is not represented in the older `clips` table and must be reviewed manually before further processing.
- Ranking is not predictive of YouTube views, and it does not verify clip context.
- All suggested candidates are review-only. Metadata, captions and upload approvals are separate existing draft workflows.
- GitHub-hosted synthetic FFmpeg tests do not prove production quality using real N3on or other streamer source material.
