# Windows local production cycle — operator opt-in

**Draft only. No service, task, OAuth token or YouTube publication is activated by the repository.** This is stacked on the unmerged PRs #17–#20. Existing private N3on Episode 2 videos remain untouched.

## Purpose

The Windows cycle invokes the existing `production_worker.run_day` via the new persisted queue. On a configured Windows PC it can create **one original-story render candidate per local calendar day** from *only* owner-approved footage and user-provided original story premises. It performs no web research, downloads, OAuth operation, YouTube upload, metadata update or public publishing.

The cycle checks the exact SHA-256 of **every** usable video file in the dedicated media directory against `approved-media.json`. Unlisted, changed, expired, symlinked, oversized or unsupported files stop the cycle. Keep source permission documents separately; a reference alone is not legal proof and an operator must review rights and finished media.

## Install Python and validate first

From PowerShell inside your existing repository folder:

```powershell
git fetch origin
git switch feature/windows-authorized-cycle
python -m pip install -r src\shadow-studio\requirements.txt
ffmpeg -version
ffprobe -version
```

Python 3.12+ and working FFmpeg/FFprobe plus a supported local voice engine are necessary for full rendering. The free `tzdata` dependency supports IANA time zones on Windows. Do not configure billing or paid APIs.

Copy `deploy/windows_local_cycle.example.json` to a private config such as `C:\ShadowStudio\worker-config.json`, and copy `deploy/approved_media.example.json` to `C:\ShadowStudio\approved-media.json`. Create `C:\ShadowStudio\original-ideas.json` as a JSON array of owner-written story premises and put only licensed, authorized media into `C:\ShadowStudio\licensed-footage`.

In `approved-media.json`, replace the sample file name, hash, written permission reference, license scope, and expiry. Calculate SHA-256 in PowerShell:

```powershell
(Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\ShadowStudio\licensed-footage\owner-approved-source.mp4').Hash.ToLower()
```

Set `enabled` to `true` **only** after verifying the listed media rights. Configure a private, user-writable `state_dir` outside the repository; back up `production.sqlite3` before merging or deploying unrelated changes. **Do not move the production database or merge drafts without review.**

## Dry-run check and schedule preview

```powershell
python src\shadow-studio\local_cycle.py --config 'C:\ShadowStudio\worker-config.json' check
powershell -NoProfile -File .\deploy\install_windows_local_cycle.ps1 -RepoRoot (Get-Location).Path -ConfigPath 'C:\ShadowStudio\worker-config.json'
```

The second command is a dry run. It does **not** install a scheduled task. It checks Python version, rights manifest and config, then prints the proposed task.

## Owner-only task activation

Only after PR CI, a clean local test, permission review and an acceptable media preview:

```powershell
powershell -NoProfile -File .\deploy\install_windows_local_cycle.ps1 -RepoRoot (Get-Location).Path -ConfigPath 'C:\ShadowStudio\worker-config.json' -Install
```

This creates one current-user, least-privilege Windows Scheduled Task named `ShadowStudioLocalProduction`, repeating every 10 minutes for one year. It does **not** overwrite an existing task. It runs only when your Windows user is logged on. The script does not request administrative elevation or credentials, and does not create any YouTube upload or public-publishing schedule.

The scheduler calls the cycle once per tick; SQLite atomic claims prevent multiple processes from taking the same pending job. Existing daily and script-duplicate controls still apply. A lost or stale lease requires review; **never blindly repeat an interrupted render**, and never repeat an ambiguous YouTube upload.

## Monitoring and safe recovery

```powershell
python src\shadow-studio\production_health.py --state 'C:\ShadowStudio\private-state' --output 'C:\ShadowStudio\private-state\health-manual.json'
python src\shadow-studio\worker_queue.py --state 'C:\ShadowStudio\private-state' status
python src\shadow-studio\worker_queue.py --state 'C:\ShadowStudio\private-state' quarantine-stale --seconds 180
```

The third command is an explicit operator action to quarantine a stale claim; check the real worker process and output folder first. It does **not** delete or retry any job. Active and resolved alerts are recorded in the existing SQLite state, and new alerts are appended to `alerts.jsonl`. The current local alert inbox is **not** a phone push, email or SMS service. The latest health report is `health-latest.json`.

**Validation scope:** GitHub Windows/Linux tests can exercise the Python queue, rights gate, alert deduplication and PowerShell script parser. They cannot prove the real Windows Scheduled Task registered or ran on your personal computer. No remote cloud host is provisioned by this work. No 8–12 hour unattended endurance test is claimed.


## Optional unattended licensed streamer highlight suggestions

After your local story render cycle is verified, you can opt in to processing additional **already licensed** streamer footage without adding any uploads.

Register each approved local file and permitted time range in the existing `streamer_clips.py` ledger first. The clip file must also appear in `approved-media.json` with the same SHA-256, a documented edit-and-repost permission reference, and a current license expiration or confirmed perpetual permission.

Add these fields to `worker-config.json`:

```json
{
  "auto_highlights": true,
  "clip_database": "C:\\ShadowStudio\\clips.sqlite3"
}
```

The complete configuration must retain the existing `enabled`, `state_dir`, `ideas_file`, `media_dir`, `rights_manifest`, and `timezone` settings. The snippet above is an example of the additional fields, not a replacement configuration.

Every Windows cycle checks the approved source inventory and enqueues each matching `clip_id` plus exact source SHA-256 **only once**. The persistent worker processes at most one queued task per 10-minute invocation. A successful scan writes 15–50-second audio-activity suggestions to the existing clip ledger for review; it does not publish, upload, change existing videos, or automatically approve the selections.

Queue failures remain `review_required`, not endlessly retried. Changes to source hashes or permission status prevent new scans. The existing clip ledger does not yet store expiry itself; the separate approved-media manifest is required as the additional expiry check.

The Windows task is never installed by these repository changes; `-Install` is still an owner action.


## Fail-closed preflight monitoring

If the task finds unlisted media, missing/mismatched hashes, expired rights, a missing clip ledger, an unavailable disk-space check or insufficient disk space, the cycle does **not** run a render. It writes a sanitized `health-latest.json` issue and adds one deduplicated event to `alerts.jsonl`. Repeated checks update an SQLite occurrence counter rather than repeatedly appending identical alerts. Once the condition clears, the alert is marked resolved.

These are **local alerts only** and are not push notifications, text messages or emails. Windows Task Scheduler does not automatically display their contents. Review the local health and alert files from the signed-in PC, or integrate an authorized notification transport later.
