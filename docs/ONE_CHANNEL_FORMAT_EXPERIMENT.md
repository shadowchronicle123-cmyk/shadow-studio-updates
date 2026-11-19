# Shadow Studio — one-channel content experiment

## Decision
Publish both **original AI story Shorts** and **rights-cleared streamer highlights** to the existing **ShadowChronicles** YouTube channel during an initial experiment. Do not create or connect a second YouTube channel without a new owner decision.

## Safety and launch gates
- The existing production worker and its first real **private** YouTube upload remain the top priority. This document does not authorize public publishing, scraping, downloading streams, or posting other creators' material.
- Streamer clips must have documented source URLs and a verified basis for reuse. A streamer permitting platform clips does not automatically establish permission for off-platform redistribution or monetization.
- Respect source platform terms; prefer official clipping/export mechanisms or owner-provided licensed files.
- Do not bypass DRM, authentication, paywalls, or anti-download controls.
- All experimental uploads start as private drafts until channel authorization, copyright checks, quality checks, and owner approval are established. Public posting requires separate authorization.
- Avoid near-identical uploads, misleading titles, or repetitive reused content. Record original commentary/editing contributions.

## Single-channel data model
Each output should carry metadata:
- `content_type`: `ai_story` or `streamer_clip`
- `experiment_id`: `shadowchronicles-format-test-1`
- `source_creator`, `source_url`, `source_start_ms`, `source_end_ms` (clip only)
- `rights_basis`, `rights_checked_at`, `rights_notes` (clip only)
- `story_premise_id` (AI stories only)
- `published_at`, `youtube_video_id`, `duration_seconds`, `upload_status`
- `editing_notes` and `content_sha256` for audit and duplicate prevention

Store separate content identifiers and per-format counters while using the **same** authorized YouTube channel. Preserve the existing one-per-day production cap until an explicit change is agreed; don't silently increase publishing frequency.

## Experiment design
- Run a balanced comparison across similar weekdays and posting windows, alternating formats when both are ready and approved.
- Avoid selecting only viral clips or only weak stories; track all attempted and rejected candidates as well as posted Shorts.
- Compare 24-hour and 7-day views, impressions where available, viewed-vs-swiped-away, average percentage viewed, watch time, subscribers gained, and estimated revenue when eligible.
- Calculate medians by format and normalize for duration and posting age. Segment clips by creator and source.
- Flag small sample sizes and channel-audience crossover; this is a practical test, not a randomized controlled trial.
- After a useful sample (suggest 10–15 published Shorts per format), recommend a mix based on retention and subscriber conversion rather than views alone. No automatic switch to a new channel.

## Implementation sequence
1. Finish current production-worker and private-upload acceptance gates.
2. Add an explicit `content_type` and experiment metadata to the ledger/API without breaking existing story jobs.
3. Add **manual, rights-cleared clip intake** first (local video, source/rights metadata), with video validation and deduplication.
4. Add highlight selection, vertical crop/facecam tracking, captions, and reviewable previews; never auto-publish unreviewed clips.
5. Add YouTube Analytics ingestion with authorized scopes, per-format reporting, and an emergency pause.
6. Consider automated source monitoring only for creators and sources with documented reuse rights and supported platform access.

## Current status
The **Licensed streamer clips** workspace in **Automation & Media Tools** accepts a local file with a creator, source URL, permission reference, and explicit editing and redistribution scope. It saves the source identity and selected time range in the local projects ledger and checks for duplicate intake. Preview rendering rechecks the source bytes, crops to 1080×1920 with left, center, or right framing, normalizes audio, and verifies the export before showing a player and MP4 download.

The recorded scope is the operator's declaration. Keep the actual license or permission evidence and confirm whether the planned use is commercial. The source link is a reference, and this workflow does not fetch or publish it.

Local acceptance covered a real preview with synthetic footage, dimension/frame/audio checks, duplicate records, invalid permission, and a source changed after intake. Remaining work includes source approval management, highlight scoring, facecam and gameplay tracking, accurate animated captions, preview history, and Analytics ingestion. Keep clip uploads behind review until those gates pass.
