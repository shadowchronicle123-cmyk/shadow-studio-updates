# Shadow Studio v0.21 — Crimson Pro / launch-readiness beta

## Changes
- Redesigned application shell, left-hand navigation on desktop, horizontal mobile tabs, clearer visual hierarchy, modern responsive cards, updated crimson gradients and accessible focus treatment.
- New production overview on Create: rendered Shorts, approved Shorts, and pending reviews.
- New read-only `/api/launch-readiness` endpoint for FFmpeg/FFprobe, optional neural voice dependency, and local review queue counts. No credentials are exposed.
- All earlier video tools, projects, batch production, quality checks, cloud handoff, and MCP server preserved.

## Not claimed
- This release does not make AI text-to-video, direct publishing, or custom ChatGPT connection operational without third-party configuration.
- Internet-dependent neural voices require a live preview on the Windows host.
- Test renders made in this development environment use a deterministic synthetic voice fixture; they do not prove provider voice quality.
