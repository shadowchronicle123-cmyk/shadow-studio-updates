# Publish a verified Shadow Studio update

The application reads `latest.json` from the official GitHub Pages update feed. GitHub Pages must use **GitHub Actions** as its deployment source for this workflow.

1. Make changes on a development branch, update `VERSION` in `src/shadow-studio/update_manager.py`, and open a pull request.
2. Wait for the Windows and Linux checks to complete. Download the candidate ZIP, independently verify its SHA-256, and check migration, rollback, and data preservation.
3. Once the specific release is approved, merge the tested commit. In GitHub Actions, open **Build and publish Shadow Studio from source** and choose **Run workflow** on `main`. Select the approval checkbox and enter the exact candidate ZIP SHA-256.
4. Wait for the release workflow to finish. It reruns smoke and offline tests, packages the source, and compares the archive to the approved SHA-256 before updating the feed.
5. Download the GitHub Release and GitHub Pages ZIPs. Compare both SHA-256 values to the release checksum sidecar and the official `latest.json`. Verify that the preceding release remains available.

On Windows, close the running Shadow Studio Command Prompt, run `update_windows.bat` in the installed folder, and start the app again after the updater succeeds.

The repository is public. Store credentials, gameplay, local projects, and renders outside the source tree. The app update does not upload or publish any videos.
