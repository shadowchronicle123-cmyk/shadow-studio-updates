# Shadow Studio v0.42.0 development progress

Development branch: `production-overhaul-v042`. Public baseline: v0.41.0 at `6ab021b`. The branch remains a release candidate until acceptance checks pass. The latest checkpoint is the head of this branch.

## Prioritized audit

1. **Release blocker — custom stories.** The default engine repeats stock dialogue and gives the prompt back as the first narrated line. The custom and random routes previously differed only by the presence of a genre.
2. **Release blocker — publishing handoff.** Cloud upload returns synchronously, has no progress callback or connection test, and conflates a successful upload with preparation for another service. Single and multiple export kits are assembled entirely in memory. The manual kit and approval hash are already useful foundations.
3. **Release blocker — conversation and render acceptance.** The gameplay crop is fixed. Gameplay always uses the same message renderer, regardless of selected style. Dialogue frames snap in, and long lines can be truncated. Existing smoke tests cover real layout and rotation but do not cover animation or preview parity.
4. **Workflow and readability.** Create bundles script generation and production into very long panels. Failed requests can show raw JSON or generic errors. Render and review views need clear next actions.
5. **Release validation.** Windows smoke tests cover startup, rendering, mixing, and the updater. Broader acceptance currently runs on Linux only and lacks a real v0.41.0 ZIP migration and browser click-through.

## Checkpoint 1

- Audited the current source layout, release flow, story/voice entry points, rendering layout, handoff, approval and major test entry points.
- Replaced the repeating custom fallback with a free authored-branch expansion mode. It preserves the premise, chooses a concrete hook and payoff, and avoids recently used plot branches when alternatives are available. Wedding/ex prompts receive context-specific branches.
- Added explicit local Ollama selection and a regenerate action. Ollama failures return an actionable error; offline expansion is labeled as authored and has a finite repertoire.
- Baseline offline integration and acceptance tests passed. Custom story tests exercise the full API with multiple variants and no paid providers.

## Next steps

1. Make the manual publishing kit the primary path. Add an S3 configuration diagnostic, read-only connection test, progress polling and retry. Test with a fake S3 client.
2. Redesign conversation frames and the creator workspace. Verify 1080p preview parity, entrance animation, clipping, caption timing and audio/video consistency.
3. Run real browser and Windows acceptance. Exercise an isolated install and rollback from the published v0.41.0 package. Open the release PR with checks and remaining external verification clearly identified.

No owner credentials, transfers to a live bucket, paid generation, or public publishing are authorized for testing. Live services require the owner’s environment and permission.

## Checkpoint 2 — handoff

- Optional cloud uploads now show check, transfer and completion states. Retries are explicit, concurrent clicks reuse the same upload, and no credentials or signed links are written into status files.
- Added local configuration instructions and a read-only bucket access test. The primary path is an approved, local publishing kit with an explicit YouTube Studio link.
- Approval is checked before transfer, after transfer, and before an in-session link is shown. A cloud link already shared outside the app lasts until expiry or revocation at the provider.
- Local tests passed for missing setup, read-only probe, simulated failure and retry, progress, duplicate upload prevention, revoked approval, and local approved MP4/SRT kit export.
- Next production blockers are animated conversation layout, adjusted gameplay framing, UI flow, and branch CI with Windows/browser acceptance. Actual cloud/vidIQ account testing remains unexecuted.

## Checkpoint 3 — creator and package candidate

- Replaced the creator UI shell with Story and Voice & Gameplay stages plus a persistent video monitor. Review, render queue, Publishing, Media Tools, and Settings have dedicated navigation. Existing projects and media routes remain available.
- Conversation layout now draws at the selected output resolution. New messages ease in from the speaker's side starting at the narration/caption cue. Preview and render use the same drawing function, including gameplay height (55%, 58%, or 60%) and crop anchoring.
- The download, review, and approval controls are connected to the finished render. Setup errors and failed requests display actionable messages. Cloud and manual handoff were checked with local/mocked fixtures.
- Local FFmpeg passed 1080×1920, 30 FPS, 8.533333 s video and 8.533000 s audio, long-line fit, rendered crop, preview parity, and animation tests. An initial crop assertion sampled the wrong part of the fixture and was corrected. Representative MP4 frames were inspected visually and the spacing was tightened to retain preceding messages.
- The real baseline v0.41.0 ZIP and candidate ZIP were used to rehearse failure rollback and a successful upgrade in a temporary install. Source, gameplay, uploads, video and project fixtures are isolated from the user's installation.
- This execution sandbox blocks Chromium's Unix process socket. Local JavaScript parsing passed. The branch CI runs the click-through on Windows using genuine Chromium, a local app process, fixture footage and synthetic voices. Windows CI separately tests native offline voice through SAPI.
- **Next:** wait for branch CI, inspect browser artifacts and any failures, fix and rerun. Review the exact candidate diff and acceptance record before considering merge into the public release workflow. Live ElevenLabs and live S3/vidIQ remain outside the authorized test scope.

## Checkpoint 4 — acceptance follow-up

- The initial Windows run at `cd84413` passed startup, custom stories, native 1080p rendering, mixing and batch exports. Its full prelaunch step failed because the test decoded and normalized the local SRT before comparing it to the archive. The comparison now uses the original bytes. Windows acceptance and browser checks are retriggered with the corrected test.
- Narration lets the owner select the main speaker independently of dialogue order. Projects retain the premise, provider, and selected speaker. The API rejects premium requests before they can reach a voice provider unless credit use was confirmed.
- The one-time preview and export share the actual uploaded footage. Video-track checks reject broken and audio-only files with a clear error. The render queue shows recent jobs, progress, errors and a review action.
- The offline engine prioritizes unused plot branches and supplies a second concrete ending for each. The wedding fixture checks eight distinct results without losing its supplied details.
- Manual kit creation verifies the exact archived MP4 bytes against the approval hash. MP4, SRT, metadata and next steps are available without storage configuration.
- Install rehearsal now also checks preservation of existing local draft files. The public feed remains on v0.41.0 while platform and browser acceptance runs.

## Review checkpoint

- Draft pull request: https://github.com/shadowchronicle123-cmyk/shadow-studio-updates/pull/1. Reviewed the source, test and package file list against v0.41.0. Resulting release candidates include no project data, drafts, uploads, secrets or temporary browser output.
- Added quality diagnostics for audio/video end mismatch, finite loudness results, and a clear response when a damaged MP4 cannot be inspected. Local prelaunch and real export acceptance passed after this change.
- On the next green run, inspect native Windows voice and browser stages specifically. Verify the exact reviewed commit, then merge only if its platform checks pass. The release workflow will publish a single v0.42.0 ZIP and SHA-256 to the feed. Verify Pages and the GitHub Release match before recommending installation.
- A second CI failure exposed a non-deterministic corruption fixture: overwriting an arbitrary compressed packet range could still decode with the Ubuntu runner's newer FFmpeg. Replaced it with an invalid H.264 keyframe length while preserving the container and audio. Local acceptance now verifies that quality diagnostics and publishing both block the damaged file.
