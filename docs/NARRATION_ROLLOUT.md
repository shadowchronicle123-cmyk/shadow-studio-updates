# Narration quality rollout

ShadowChronicles uses a stable narrator identity. Creator comparisons should use permitted public metadata and human observations of expressive pacing, pauses, pronunciation and clarity; never download or clone competitors' voices.

## Current production options

- Existing production CLI: `SHADOW_NARRATION_ENGINE=edge-neural python production_worker.py run ...` selects Edge neural voices, using `SHADOW_NEURAL_PRIMARY_VOICE` and `SHADOW_NEURAL_SECONDARY_VOICE` for stable identities. Network access is required; no paid API key is required.
- Default `espeak` remains available for offline development/testing, **not approved for unattended upload**.
- The separate `unattended_private_upload.py` entrypoint requires `SHADOW_APPROVED_VOICE_IDS` in the private worker environment, plus `SHADOW_NARRATION_REVIEW_APPROVED=1` and `SHADOW_APPROVED_NARRATION_SHA256=<64-character digest>` in private worker configuration, a valid narration WAV, and a matching SHA-256 in the review manifest. A generated manifest cannot grant approval. This is a gate, not proof of human likeness.
- The production worker now exports `renders/<day>/narration.wav` and `narration-review.json` with a SHA-256 digest. All worker approval settings default to absent, so unattended upload remains blocked until explicitly reviewed and configured. The existing `private_upload.py` entrypoint is not protected by this new gate; production scheduling must invoke only the gated entrypoint.
- An approved voice must first be listened to by the owner. Do not set `SHADOW_APPROVED_VOICE_IDS` based on automated tests alone.

## Acceptance criteria

1. Create voice samples using actual neural synthesis and compare pacing, pronunciation and emotional delivery against reference observations.
2. Listen to and explicitly approve a consistent narrator voice; store the allowlist only in worker configuration.
3. Validate the new narration stem and SHA-256 in cloud rendering tests; strengthen binding to the verified MP4/export ledger and protect the manifest from untrusted modifications.
4. Audit all other upload paths and store reviewed MP4 and narration digests in a durable, authenticated approval record. The legacy daily CLI now requires the review gate.
5. Run a real private-only upload with owner consent, verify the exact channel and private visibility, and only then consider scheduling.

Cloud tests are offline and cannot establish naturalness or successful Google authorization.

## Per-video approval

For each video, listen to the exported clean `narration.wav`, review the finished MP4, then set `SHADOW_APPROVED_NARRATION_SHA256` to that WAV's SHA-256 in the private worker environment. A voice allowlist alone is not enough. Do not place owner approval settings in the generated `narration-review.json`. The owner must repeat this approval for each new audio track; this is **not** yet a fully hands-off daily workflow. The gated unattended entrypoint now requires `SHADOW_APPROVED_VIDEO_SHA256` to match the verified MP4 ledger fingerprint as well as `SHADOW_APPROVED_NARRATION_SHA256` to match the narration WAV. A durable approval ledger, review UI, and a complete audit of manual upload paths remain future work. The legacy `upload_daily` entrypoint now calls both owner-approval verifiers and fails closed.
