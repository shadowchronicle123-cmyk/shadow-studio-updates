# Shadow Studio v0.18 — ChatGPT / vidIQ handoff

This version adds a downloadable, per-video handoff ZIP in the Library. Each package contains the approved MP4, metadata, checksum, an explicit instruction for ChatGPT to create a vidIQ draft, and clear approval safeguards. No automated transfer or public publishing occurs.

## Steps
1. Start the local app as before.
2. Open **Library**, watch a finished Short, inspect its quality report, and mark **Approved**.
3. Click **ChatGPT + vidIQ handoff ZIP**.
4. Extract the ZIP and attach `short.mp4` to ChatGPT; paste `REQUEST_FOR_CHATGPT.txt`.
5. Review the vidIQ draft and explicitly approve publishing separately.

## Security
This is a manual file handoff; it is not a direct ChatGPT connector. The ZIP contains no credentials. The protected `/api/agent` endpoints remain disabled unless a 32+ character owner token is configured. Keep the server on localhost and don't publish it directly to the internet.

## Testing limits
The handoff functionality can be tested with an already approved MP4 and a simulated quality-check report. A real YouTube publish requires vidIQ access and the user's explicit final approval. Neural voice availability also requires separate Windows testing.
