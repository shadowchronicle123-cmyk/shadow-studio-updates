# Shadow Studio v0.19 — Private Cloud Handoff

**What works:** With private S3-compatible bucket credentials in `.env`, approved and quality-checked Shorts can be uploaded on demand or automatically after approval. The app returns a temporary 24-hour HTTPS link and a complete ChatGPT + vidIQ import request. This avoids manually uploading a video attachment in chat. The owner can use Copy and paste one request into ChatGPT. Tested with mocked S3 and local quality check.

**What does not work yet:** ChatGPT does not independently connect to a private PC, pull cloud uploads on its own, or start a vidIQ workflow without a user turn. A remote private storage account is required, and authentic cloud transfer needs your credentials and live integration testing. Publishing always requires a second explicit approval.

## Setup
1. Upgrade the app and `pip install -r requirements.txt`.
2. Set SHADOW_S3_BUCKET, SHADOW_S3_REGION, SHADOW_S3_ACCESS_KEY and SHADOW_S3_SECRET_KEY in a local `.env`; set SHADOW_S3_ENDPOINT to the full HTTPS endpoint for S3-compatible storage such as R2. Use a **private** bucket and least-privilege key.
3. Open an approved video in Library and click **Generate private video link**. Paste the produced request into your ChatGPT conversation.
4. Optional: set SHADOW_HANDOFF_AUTO_UPLOAD=true to upload automatically **after** video approval. It does not post to YouTube or invoke ChatGPT.

**Privacy:** Temporary links permit access to whoever possesses them and expire after 24 hours. Uploaded objects remain in your bucket after link expiry until you delete them or configure a storage lifecycle rule. Don't share your `.env` or paste storage access keys into ChatGPT.
