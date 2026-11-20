# ShadowChronicles cloud-only worker — Phase 1

Status: development branch, NOT deployed, no publishing capability.

## Hosting assessment

Oracle Cloud Always Free ARM (Ubuntu aarch64) is a candidate, not a guaranteed free or available host. Always Free tenancies currently document a total of 2 OCPUs and 12 GB RAM for ARM, subject to region availability and idle-instance reclamation. Check the console's Always Free eligibility before provisioning. Never enable paid resources without explicit approval.

GitHub Actions remains for CI and evidence, not an unlimited production rendering host.

## Architecture

Daily scheduler -> SQLite idempotent job -> local/offline story -> voice rotation -> (future) audio + FFmpeg render -> (future) QA gate -> (future) private YouTube upload -> (future) standing-authorized public upload.

Phase 1 implements ONLY the first four components. It does not synthesize speech, render MP4s, connect to YouTube, spend credits, or deploy a server.

## Local smoke test

From `src/shadow-studio` with dependencies installed:

```sh
python cloud_worker_test.py
python cloud_worker.py --once --database worker-data/jobs.sqlite3
python cloud_worker.py --once --database worker-data/jobs.sqlite3
```

The second invocation must return `reused: true` with the same story and voice pair. SQLite data must be stored outside publicly served folders and backed up securely.

## Required before enabling unattended publishing

- Linux ARM FFmpeg and TTS runtime tested on the actual host
- Originality and content-policy checks, plus video and audio QA
- Secure YouTube OAuth with minimal scopes and no secrets in git
- Private-upload trial, duplicate protection, per-day limits and emergency pause
- Explicit standing authorization for public publishing
- Budget guardrails and monitoring
- Validate that gameplay/music assets are licensed for the channel

Do not claim this Phase 1 planner is a complete production or publishing service.
