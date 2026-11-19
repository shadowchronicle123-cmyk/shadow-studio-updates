# Shadow Studio v0.27 — Narration + handoff acceptance

## Implemented
- Deterministic, cached narrator preview WAVs keyed by voice and speaking rate. Repeating the same preview reuses local audio instead of another request to a paid provider; first previews still require the configured voice service.
- The voice selector correctly identifies Matilda only when your ElevenLabs account is separately configured and authorized. Matilda remains the preferred voice in vidIQ; the free local Edge neural voices are alternatives and will not sound identical.
- The local dashboard explicitly explains vidIQ-versus-ElevenLabs access and provider charges. No silent fallback to the robotic voice.
- Existing Crimson Studio, quality gate, captions, local approval queue, and read-only MCP bridge retained.

## Tests run here
- `python integration_acceptance_v027.py`: passed. Validated local voice-preview caching with a mocked narrator, MCP tool authentication and read-only operations, real vertical MP4 with synthetic audio and captions, corrupt MP4 blocking and approved-only handoff.
- `python -m compileall -q .`: passed.
- The generated MP4 in the test suite is **not** a test of the actual sound of an online natural voice.

## Live steps not completed
- Natural voice network call on your Windows PC and your personal listening review.
- Private S3/R2 storage credentials, remote MCP OAuth/account authorization, and direct automatic delivery to this exact ChatGPT conversation.
- Live vidIQ video import and YouTube draft from a newly approved Shadow Studio export; publishing still requires explicit approval.
- Real Windows installation and owner account access.

## Tonight
1. Run the app on Windows from the inner `shadow-studio` directory as documented in README.
2. Go to Create > Narrator voice and select Jenny, Aria, or Michelle; press Preview voice. If it cannot connect, check internet and the Integration Health Center.
3. Render ONE 720p story, inspect captions and listen throughout, and approve in the Library only if it is acceptable.
4. Prepare the private video link (requires cloud configuration) or download its MP4 and attach in ChatGPT for a vidIQ draft; approve the draft separately before publishing.

**Security:** no premium API keys are included in the ZIP. Do not share `.env`. Keep the server on `127.0.0.1` and do not expose local ports.
