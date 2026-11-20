# Private campaign review and scheduling (draft)

This is an *additive* command-line workflow for existing Shadow Studio production data. It builds on the unmerged R3born draft PR #17. **No public publishing endpoint is used or enabled.** No service, timer, live upload or paid provider is activated by installing this PR.

## Already uploaded videos: do not upload again

The three existing verified private IDs are `k35fTGK9erE`, `dv44eSDgdXY`, and `GK1aJgt3Xtw`. Their historical local SQLite ledger is the source of truth. These GitHub changes do not reconstruct or overwrite the user's local ledger. Never re-register these MP4 files for a second upload merely to demonstrate the workflow.

## Windows PowerShell: inspect and test safely

From the repository checkout, with Python 3.12+ and FFprobe installed:

```powershell
git fetch origin
git switch feature/review-scheduling-safe-queue
py -m pip install -r src\shadow-studio\requirements.txt
Push-Location src\shadow-studio
py test_r3born_campaign.py
py campaign_review_test.py
py production_worker_test.py
py private_upload_test.py
$state = Join-Path $HOME 'Documents\shadow private'
py campaign_review.py --state "$state" status
Pop-Location
```

Run those tests before enabling scheduled transfers. The tests do not call YouTube. GitHub CI additionally covers Windows Server and Linux for source/regression tests; confirm the newest commit's actual CI status before merging.

## Review one NEW, authorized captioned clip

The existing `r3born_campaign.py register` operation requires an MP4, the matching SHA-256, a title, and documented permission to edit and repost. It validates FFprobe media structure; an actual reviewer must still verify the captions, original audio, rights, and truthful title. Up to three registered slots per day are supported.

```powershell
Push-Location src\shadow-studio
$state = Join-Path $HOME 'Documents\shadow private'
$day = Get-Date -Format yyyy-MM-dd
$clip = 'C:\Media\approved-new-short.mp4' # Replace with a NEW licensed, reviewed clip
$hash = (Get-FileHash -LiteralPath $clip -Algorithm SHA256).Hash.ToLower()
py r3born_campaign.py --state "$state" register --day $day --file "$clip" --title "Accurate title describing this clip" --permission-reference "Documented authorization to edit and repost" --sha256 $hash
# Replace 1 with the registration command's actual Reviewed slot number:
py campaign_review.py --state "$state" approve --day $day --slot 1 --note "Watched full clip; confirmed permission, audio, captions and title"
py campaign_review.py --state "$state" schedule --day $day --slot 1 --at "2035-11-22T14:00:00-05:00"
py campaign_review.py --state "$state" status
Pop-Location
```

The timestamp must be in the future and include a UTC offset. There are at most three scheduled handoffs per UTC day, even if registered on different dates. Other review commands are `reject` and `unschedule`. Every decision is written to `r3born_review_events` in the existing production SQLite file.

## Explicit private transfer (do not run as a test)

The command below is a real YouTube upload, **not a simulation**. It only sends due, preapproved, scheduled slots, requests the already configured OAuth credentials, rechecks the exact in-memory MP4 checksum, checks the target channel, and uses the existing private-only resumable transport. A previous response ambiguity remains blocked for operator reconciliation.

```powershell
Push-Location src\shadow-studio
$state = Join-Path $HOME 'Documents\shadow private'
$credentials = Join-Path $state 'youtube.json'
py campaign_review.py --state "$state" dispatch-due --credentials "$credentials" --confirm-private --audience not-made-for-kids
Pop-Location
```

Do **not** turn this into a Windows Scheduled Task until full QA, credentials, permission documentation, channel verification and private handoff on an explicitly approved *new* clip are complete. When ready, the operator may run the same command from Windows Task Scheduler with a suitable cadence; scheduling a private upload does not schedule public YouTube publication. Automatic public publishing is not implemented.

## Remaining work

- Connect the existing approved-clip review/handoff draft PRs #14–#16 after their security and integration reviews.
- Licensed source URLs must provide an authorized downloadable file plus a trusted SHA-256. The current downloader disallows redirects; it does not scrape YouTube/Twitch or bypass access controls.
- Speaker-accurate transcription, ranked highlights, truthful automated title suggestions and an integrated review UI are not implemented in this PR. Existing FFmpeg caption and preview tools remain separate.
- Review the user's actual local campaign ledger and private YouTube IDs before any retry or migration.
- A successful CI run proves isolated test assertions, not live YouTube authentication, real copyrighted-footage authorization, or a completed Windows desktop/operator deployment.
