# Shadow Studio v0.20 — Private ChatGPT MCP bridge

## Built

- Separate read-only MCP HTTP server (`shadow_mcp.py`), listening only at `127.0.0.1:8766`.
- MCP initialize, tool listing and calls, including `list_approved_videos`, `get_approved_video`, and `get_approved_video_link`.
- Strong approval + current technical quality gate enforced for all video records.
- Cloud link tool only signs an EXISTING, verified private cloud upload, never performs an upload.
- One-hour presigned link only returned to authorized clients; no file paths or storage credentials included.
- Authentication off by default until a 32+ character local owner token is configured. OAuth JWT validation is available for remote integrations and requires configured issuer, JWKS URL, resource audience, owner subject, and read scope.
- No editing, video approval, cloud upload, social posting, or publishing tools exposed.

## Important limitations

**This does NOT itself install a plugin into your ChatGPT account or send unsolicited messages into this conversation.** A live ChatGPT custom MCP connection must be created using an eligible account and a supported remote connection (such as Secure MCP Tunnel). It requires a supported OAuth 2.1 provider and user sign-in (ChatGPT cannot use a manually provided API token as MCP OAuth authentication). The included OAuth resource metadata and JWT validation are not a complete authorization server. We cannot verify the real account integration without your authorization and live credentials.

## Windows quick start

1. Extract as a replacement project directory (back up prior `.env` and your `outputs`, `gameplay`, `projects`, and uploaded media; migrate those folders carefully).
2. Install requirements from `requirements.txt` in `.venv`.
3. Create `.env` locally, starting from `.env.example`. Generate SHADOW_AGENT_TOKEN with `python -c "import secrets; print(secrets.token_urlsafe(36))"`; keep it secret.
4. Start `start_mcp_windows.bat` in addition to your usual Shadow Studio dashboard. `/health` at `http://127.0.0.1:8766/health` is a safe local health endpoint.
5. Use a supported OAuth provider and Secure MCP Tunnel for actual ChatGPT integration; connect the tunnel-backed MCP endpoint from ChatGPT **web** Plugins if your account supports custom MCP connections. Enable read-only tools only. Avoid tunneling port 8765: that editing interface has owner-local endpoints and is not designed for public access.

Official docs:
- https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://developers.openai.com/plugins/build/auth

## Publishing flow after connection

Ask ChatGPT to list approved Shadow Studio videos and get a fresh approved video link. ChatGPT can then use connected vidIQ tools to import the MP4 and prepare a draft. **You must approve the exact vidIQ draft separately before any public publishing.**
