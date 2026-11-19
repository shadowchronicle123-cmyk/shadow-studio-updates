# Shadow Studio 0.43 candidate

PR #2 is the development and QA branch. The owner approved v0.43.0, and the official channel now carries that release. No release or update-feed changes are authorized by the QA workflow.

## Audit and repair

- Original compatibility run 44549777412 passed three of four combinations. Server 2022/Python 3.12 failed with WinError 183 on an updater backup path reused within the same clock tick. The new updater allocates unique folders and tests two attempts at one frozen timestamp.
- Migration now uses the checksum-verified v0.42.0 archive and verifies rollback, restart and unchanged project, media, drafts and configuration fixtures. The legacy updater's two attempts use distinct frozen ticks because that shipped code predates the fix.
- Trimmed gameplay loops only the selected time range, uses the same in-point for preview, and validates invalid trims before narration. Trim settings are saved with projects.
- The free offline story engine now has distinct roommate/rent and coworker/shift plot branches and preserves the supplied premise. Existing authored plots remain finite.
- Candidate builds run Windows Server 2022 and 2025 with Python 3.12 and 3.13 plus Linux. Playwright attaches synthetic video and screenshots. Publishing workflows now require explicit manual dispatch; the source workflow also checks the exact approved candidate SHA-256 before writing release files.

## Completion gate

Wait for PR #2 Windows and Linux jobs, inspect failures and rerun as needed. Download the candidate artifact, independently verify its ZIP and SHA-256, and verify the candidate manifest, migration and public v0.42.0 feed. Ask the owner for approval before any GitHub release or official feed update. Cloud Windows Server tests do not represent actual Windows 10/11 desktop testing. Paid and live publishing integrations are outside the QA scope.

## Released — v0.43.0

- PR #2 merged as `fc3593129b16396237ed6fe0e4b6444cfc00c6e1`. The source matched the candidate tested in [cloud QA run 44551553470](https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/actions/runs/44551553470).
- The owner-approved [release run 44560295012](https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/actions/runs/44560295012) passed Windows smoke and Linux tests, matched the approved archive digest, deployed the Pages feed, and published [v0.43.0](https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/releases/tag/v0.43.0). SHA-256 is `4b6bca128009f063f3c8912d3869226a53b0c2d34ac1ede377d6c8443222f129`.
- The connected GitHub interface lacked workflow dispatch. A one-time trigger bound to this approved digest started the same release workflow. The normal manual trigger was restored after publication.
- Cloud QA rehearsed the v0.42.0 migration and injected failure rollback, including data preservation. Windows 10/11 desktop testing remains open. The [v0.42.0 release](https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/releases/tag/v0.42.0) remains available.
- [PR #4](https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/pull/4) contains the separate worker checkpoint. Benchmark a free Linux host with owned footage, run the scheduled production pipeline, and verify a live private upload before describing it as operational.
