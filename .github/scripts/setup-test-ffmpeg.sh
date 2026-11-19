#!/usr/bin/env bash
# Keep Linux acceptance independent of slow package mirrors when FFmpeg is absent.
set -euo pipefail
if command -v ffmpeg && command -v ffprobe; then
  exit 0
fi
asset=ffmpeg-n8.1.3-36-ge22a4510c9-linux64-gpl-8.1.tar.xz
sha256=fce424851b0a982bdd4ac4e96f0e7b32cf29de999e54500676d41ad175ed0c79
url=https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/autobuild-2026-11-18-12-39
archive="$RUNNER_TEMP/$asset"
destination="$RUNNER_TEMP/shadow-test-ffmpeg"
if curl --fail --silent --show-error --location --connect-timeout 10 --retry 1 --max-time 60 "$url/$asset" -o "$archive" &&
   printf '%s  %s\n' "$sha256" "$archive" | sha256sum --check; then
  mkdir -p "$destination"
  tar --extract --xz --file="$archive" --directory="$destination" --strip-components=1
  "$destination/bin/ffmpeg" -version | sed -n '1p'
  "$destination/bin/ffprobe" -version | sed -n '1p'
  echo "$destination/bin" >> "$GITHUB_PATH"
else
  sudo apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=20 update
  sudo apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=20 install -y ffmpeg
fi
