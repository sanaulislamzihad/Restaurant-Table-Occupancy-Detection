#!/usr/bin/env bash
# Fake CCTV camera: loops a video as a live RTSP stream (MediaMTX + ffmpeg).
# Usage: ./fake_camera/start_camera.sh <video file name in VIDEOS_DIR, or a path>
# Settings are read from backend/.env. See start_camera.py for details.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python=""
for candidate in "$root/.venv/bin/python" "$root/.venv/Scripts/python.exe"; do
  if [ -x "$candidate" ]; then
    python="$candidate"
    break
  fi
done
if [ -z "$python" ]; then
  python="$(command -v python3 || true)"
fi
if [ -z "$python" ]; then
  echo "Python 3 not found. Create the virtual environment first (see README)." >&2
  exit 1
fi
exec "$python" "$root/fake_camera/start_camera.py" "$@"
