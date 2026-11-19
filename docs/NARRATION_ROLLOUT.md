# Narration quality rollout

ShadowChronicles uses a stable narrator identity. Creator comparisons should use permitted public metadata and human observations of expressive pacing, pauses, pronunciation and clarity; never download or clone competitors' voices.

## Current production options

- Existing production CLI: `SHADOW_NARRATION_ENGINE=edge-neural python production_worker.py run ...` selects Edge neural voices, using `SHADOW_NEURAL_PRIMARY_VOICE` and `SHADOW_NEURAL_SECONDARY_VOICE` for stable identities. Network access is required; no paid API key is required.
- Default `espeak` remains available for offline development/testing, **not approved for unattended upload**.
- The separate `unattended_private_upload.py` entrypoint requires `SHADOW_APPROVED_VOICE_IDS` in the private worker environment, plus a valid narration WAV and a matching review manifest. This is a gate, not proof of human likeness.
- The production worker does **not yet export** `renders/<day>/narration.wav` or `narration-review.json`; unattended upload is intentionally blocked until this is implemented and tested. The existing `private_upload.py` entrypoint is not protected by this new gate; production scheduling must invoke only the gated entrypoint.
- An approved voice must first be listened to by the owner. Do not set `SHADOW_APPROVED_VOICE_IDS` based on automated tests alone.

## Acceptance criteria

1. Create voice samples using actual neural synthesis and compare pacing, pronunciation and emotional delivery against reference observations.
2. Listen to and explicitly approve a consistent narrator voice; store the allowlist only in worker configuration.
3. Export a verifiable narration track and review metadata with each finished video; bind them cryptographically to the approved export.
4. Make all unattended upload routes pass the gate; prevent legacy upload CLI from bypassing it.
5. Run a real private-only upload with owner consent, verify the exact channel and private visibility, and only then consider scheduling.

Cloud tests are offline and cannot establish naturalness or successful Google authorization.
