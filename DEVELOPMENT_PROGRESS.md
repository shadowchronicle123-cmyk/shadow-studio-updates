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
