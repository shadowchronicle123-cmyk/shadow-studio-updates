# ShadowChronicles production integration

This branch brings the story worker and licensed clip previews into one Shadow Studio build. The public updater remains on v0.43.0. The publishing target is the existing ShadowChronicles channel.

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
- The combined browser test opens the normal production app, exports a story, and renders a rights-checked clip preview. It records synthetic evidence. Network OAuth and upload tests use mock responses.

## Next checkpoint

1. Finish the combined Windows and Linux checks and review generated MP4 evidence. Fix any failed browser or FFmpeg case before merging.
2. Run the deployment rehearsal on a confirmed free Linux host. Complete the single existing channel’s OAuth consent and inspect the first unattended private upload in YouTube Studio.
3. Add clip review and approved-source management before scheduling clip uploads. Keep public publishing disabled until separately authorized. Ship an app update after validating the complete upgrade and rollback.

No credentials, owner footage, public posts, paid resources, or channel changes are part of this branch.
