# Shadow Studio automatic releases

## Current status
- The verified v0.29 application source is tracked in `src/shadow-studio/`.
- `.github/workflows/build-from-source.yml` compiles the Python files, builds the app ZIP, verifies integrity and computes SHA-256.
- A newer `VERSION` in `src/shadow-studio/update_manager.py` is required before the workflow publishes a release. This prevents accidental release of incomplete changes.
- The build publishes `docs/shadow-studio-v<version>-source.zip` and updates `docs/latest.json`.
- The deployed HTTPS manifest is consumed by Shadow Studio's existing `SHADOW_UPDATE_FEED`.

## One-time GitHub Pages setting
Because release files are committed by GitHub Actions, branch-based Pages builds may not run from those bot commits.

In **Settings → Pages → Build and deployment**, choose **GitHub Actions** as the Pages source. The `build-from-source.yml` workflow is configured with the supported Pages artifact/deployment actions for new releases.

## Publishing a version
1. Edit the source in `src/shadow-studio/` and run applicable tests.
2. Change `VERSION` in `src/shadow-studio/update_manager.py` to the new version (e.g. `0.30.0`), only after the changes are ready.
3. Commit to `main`. The build runs automatically.
4. Check **Actions → Build and publish Shadow Studio from source** for successful build/deployment.
5. Check the GitHub Pages `latest.json` URL matches the newly published version.
6. On the Windows PC choose **Check for updates**, stop the server, then run `update_windows.bat`.

## Privacy and safeguards
This repository is **public**. Source code in `src/shadow-studio/` is publicly readable. Keep local `.env`, credentials, personal projects, rendered media, and gameplay uploads off GitHub. The release build excludes those paths. The update ZIP is checked before installation. Publishing a release does not upload to YouTube.

The v0.30 package created outside GitHub is **not yet part of this source tree**. Do not set v0.30 as live until its changes have been committed and the pipeline has passed.
