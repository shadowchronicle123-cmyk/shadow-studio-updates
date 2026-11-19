# ShadowChronicles production worker

Branch `feature/shadowchronicles-worker` builds on PR #2. The worker defaults to local rendering with eSpeak NG and FFmpeg. It does not invoke network AI or paid voice APIs. Initial YouTube testing uses a separate manually confirmed private handoff command. QA uses the existing Windows matrix and Linux job. Production runs on a separate Linux host under the supplied systemd timer, independent of a personal PC and of GitHub Actions.

## Implemented and tested

- A SQLite ledger claims at most one job per day in the configured time zone. Concurrent workers cannot claim the same day. A running job blocks a later day until it is resolved. The worker selects unused owner-provided premises, rotates eSpeak voice pairs and selects only from the owner-curated footage directory.
- The worker runs the existing Shadow Studio renderer at 1080×1920 with captions. Structural checks reject duplicate lines, repeated scripts and missing speaker variety. A result must pass FFmpeg quality analysis, 30 FPS, a 30–60 second duration and caption timing. Each daily job gets up to two attempts. After an emergency stop at the next checkpoint, the candidate stays out of the ready output folder.
- **Daily Production** displays the ledger and controls a persistent emergency stop. Point `SHADOW_PRODUCTION_STATE` to the same directory the worker uses. Keep the dashboard on localhost and access it through a private SSH tunnel if hosted remotely. A stopped or failed daily job does not consume another daily slot, and the worker never overwrites existing approved outputs.
- The local acceptance test exercised the daily limit with concurrent claims, voice and footage rotation, the emergency switch, transient failure and retry, and a real 1080p MP4 with synthetic narration. Linux CI tests with real eSpeak NG.

The private-only transport passed mocked tests for exact-byte approval, reported private visibility, stop, credential non-persistence, and failure deduplication. The dashboard offers a download of ready MP4s and shows private upload status.

## Hosting assessment

**First provider to benchmark: Oracle Cloud Always Free A1.** Its current free-tenancy documentation lists 1,500 OCPU-hours and 9,000 GB-hours per month, equivalent to 2 OCPUs and 12 GB, 200 GB combined boot/block storage and 10 TB monthly outbound transfer. Availability varies by home region and Oracle can reclaim idle free instances. Use an eligible free tenancy and explicit free resources. Do not upgrade the account or substitute paid shapes. A small daily workload is vulnerable to idle reclamation, so keep a copy of the input catalog and ledger. [OCI Always Free limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

**Google Cloud fallback to evaluate, with billing safeguards still required.** Compute Engine's eligible free e2-micro has 30 GB standard persistent disk and 1 GB monthly outbound transfer. A 50 MB Short every day would use about 1.5 GB of egress in 30 days, before previews or other traffic. Cloud Scheduler provides three free jobs per billing account, but this does not remove compute, disk or egress charges. [Compute Engine pricing](https://cloud.google.com/products/compute) · [Scheduler pricing](https://cloud.google.com/scheduler/pricing)

**GitHub Actions stays QA-only.** Hosted jobs have a six-hour ceiling, and scheduled runs can be delayed or dropped. Production needs persistent state and predictable scheduling. [Actions limits](https://docs.github.com/en/actions/reference/limits) · [Scheduling behavior](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)

No VM or billing account has been provisioned. The app's $0 paid-provider limit is enforced through local-only narration. Infrastructure spend remains a deployment constraint and must be checked independently.

## Deployment and remaining release gates

1. On an eligible free Linux host install Python 3.12+, FFmpeg, FFprobe and eSpeak NG. Prepare the app and venv under `/opt/shadow-studio`. Provide a JSON array of original premises under `~/.config/shadowchronicles/ideas.json` and owned/licensed footage under `~/.local/share/shadowchronicles/gameplay`.
2. Run the worker manually and benchmark 1080p processing, file sizes and disk consumption. Copy `deploy/shadowchronicles.service` and `.timer` to the user's systemd units and enable only after the host passes. The template runs at 9 a.m. Eastern. It has a 30-minute hard runtime limit. After suspend/restart it can catch up once, subject to the ledger. A force-killed job can leave a running record, which intentionally blocks further claims. Review the directory and ledger before recovering it.
3. Verify the first private upload live. The separate `private_upload.py` CLI requires `--state`, `--day`, the reviewed MP4's `--sha256`, `--confirm-private`, an explicit `--audience`, and an authorized owner-channel OAuth access token in `SHADOW_YOUTUBE_ACCESS_TOKEN`. It initiates a resumable request with private visibility, records the verified video ID, and quarantines ambiguous results to prevent a duplicate insert. Reconciliation and token refresh are operator steps. The timer does not invoke uploads. The transport has been tested with mocks only. Unverified API projects created after July 28, 2020 have private-only uploads pending a compliance audit. [YouTube videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)
4. Add analytics and publishing history only after the private upload test. Scheduling public releases requires separate owner authorization. The existing checks are structural and technical. Reviewing originality, appropriateness, and rights still needs a person. Offline story branches are finite and eSpeak voices are basic.

Production has never run unattended on a provisioned host or uploaded to YouTube.
