# ShadowChronicles production integration

PR #9 merged as `b63ac5eb7d305c1fa5211d6022b5315f3dff9ef4` on `main` and brings the story worker and licensed clip previews into one Shadow Studio build. The public updater remains on v0.43.0. The publishing target is the existing ShadowChronicles channel.

| Area | Implementation | Remaining acceptance |
| --- | --- | --- |
| Daily stories | One job per local day, owner-curated premises and footage, offline eSpeak voice rotation, checksummed 1080×1920 outputs, captions, duplicate detection and emergency pause | Benchmark on the chosen free host with authorized production inputs |
| Private uploads | Desktop OAuth consent setup, refresh token stored outside the repository, pinned channel, systemd trigger, same-session recovery and duplicate protection | Complete the owner's Google consent, verify a scheduled run plus private upload in YouTube Studio |
| Streamer clips | Permission record and source digest, segment deduplication, vertical crop, loudness normalization, verified MP4 preview and browser download | Creator approval registry, highlight ranking, tracking, accurate animated captions and persistent review history |
| Experiment | Existing single-channel plan defines a balanced comparison by weekday and posting time | Store publication IDs with format and creator; ingest views, retention, subscribers and optional revenue using authorized Analytics scopes |

## Branches and evidence

- [PR #4 worker](https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/pull/4) at `010cb7084f9bf4bff1a579262d447aca3faf6eec`.
- [PR #5 clip preview](https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/pull/5) at `45501d358d06069f6444fb742b8aff8c6306d263`.
- See [worker deployment and QA](WORKER_PROGRESS.md) and the [experiment and clip workflow](docs/ONE_CHANNEL_FORMAT_EXPERIMENT.md).
- [Combined cloud QA](https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/actions/runs/44566529088) passed four Windows Server/Python jobs and Linux, including real eSpeak narration, the 30–60-second worker export, and a mocked private upload of the exact rendered bytes. The [production acceptance workflow](https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/actions/runs/44566529529) also passed Windows and Linux.
- The [Windows browser evidence](https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/actions/runs/44566529088/artifacts/14553040806) includes both verified 1080×1920, 30 FPS MP4 fixtures. The separate story editor export is 28.033 s and the streamer preview is 3.500 s. Both have matching audio and video durations.

## Next checkpoint

1. Run the deployment rehearsal on a confirmed free Linux host. Complete the single existing channel’s OAuth consent and inspect the first unattended private upload in YouTube Studio.
2. Add clip review and approved-source management before scheduling clip uploads. Keep public publishing disabled until separately authorized. Ship an app update after validating the complete upgrade and rollback.

No credentials, owner footage, public posts, paid resources, or channel changes are part of this branch.

## Story export follow-up

- Daily output now includes a title, description, tags and content format in `metadata.json`. Technical acceptance rejects invalid caption intervals and sustained near-black frames before marking the video ready.
- PR #6 has green intake safety checks. PR #7 has green pure caption planning checks. PR #8 carries the overlapping caption planner plus FFmpeg and transcription adapters, so integrate the caption stack once after reviewing its latest commit and confirming genuine model inference. The independent branches were kept intact.
- A persistent Linux host, owner-controlled Google Desktop OAuth consent and authorized production footage are still required for a live private upload rehearsal. The worker uses the existing ShadowChronicles channel only.

## Unified review checkpoint

Daily Production shows the daily story worker and the latest 50 verified
streamer clip previews. The clip list and review decisions survive app
restarts. Approval verifies the exact render bytes against the checksum
recorded after quality checks. Existing preview MP4s stay downloadable by
their original link. Earlier renders without a recorded checksum need to be
rendered again to appear in the review queue.

Clip decisions are local review metadata. The existing uploader still accepts
its own verified story exports only. Scheduled clip production, live
transcription, clip analytics, and hosted dashboard access remain open. This
change uses neither a paid provider nor YouTube credentials.

## Approved clip handoff

An approved clip can be downloaded as a private handoff ZIP from Daily
Production. The app re-runs the technical check, verifies the exact approved
bytes while writing the archive, and includes a private ShadowChronicles
manifest with content type, creator, clip range and digest. Permission
references and original source URLs remain in the local rights ledger.

The ZIP is a local deliverable. The production uploader continues to accept
only verified story exports. A future clip transfer must add server-side
approval, cross-format duplicate protection and explicit private upload consent.

## Private clip worker checkpoint

The existing private-only uploader now accepts a local approved clip ID,
explicit checksum and audience, and uses the same persisted daily slot as
story uploads. It checks recorded edit-and-repost rights and current technical
quality, and rechecks approval before initial or resumed transfer. The
trusted local review database is required. A ZIP alone grants no authority.

For a manually approved test on the worker host, use
`python private_upload.py --state WORKER_STATE --credentials LOCAL_YOUTUBE_JSON --clip-id VIDEO_ID --projects PROJECTS_FOLDER --clips CLIPS_FOLDER --sha256 REVIEWED_SHA256 --confirm-private --audience AUDIENCE --timezone WORKER_TIMEZONE`.
Use the real audience classification and the worker's existing scheduling
timezone. Keep credentials on the worker host. This command is not connected
to the unattended timer. Story jobs retain their existing workflow.

The one-slot-per-day cap is shared across clip and story transfers for the
same ledger day. Higher-volume scheduling and independent QA quota estimates
are not wired to live uploads. The Netlify OAuth start route returned 401
from the development environment. Live Google worker credentials and a
licensed production source were unavailable in that environment.
