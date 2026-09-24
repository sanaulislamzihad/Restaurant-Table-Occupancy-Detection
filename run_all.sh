#!/usr/bin/env bash
# Starts the backend (which starts MediaMTX itself) and the dashboard, and opens
# the dashboard in the browser. Do the setup steps in README.md once first.
# Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=.venv/bin/python
if [ ! -x "$PYTHON" ]; then
    echo "The Python environment .venv is missing. Do the setup steps in README.md first."
    exit 1
fi
if ! command -v npm >/dev/null; then
    echo "npm was not found. Install Node.js (see README.md), then run this again."
    exit 1
fi
if [ ! -x fake_camera/bin/mediamtx ]; then
    echo "Downloading MediaMTX..."
    "$PYTHON" fake_camera/download_mediamtx.py
fi

# Install the dashboard packages when some are missing or out of date.
(cd frontend && { npm ls --depth=0 >/dev/null 2>&1 || npm install; })

# OpenCV prints only fatal FFmpeg messages; the backend logs connection problems itself.
export OPENCV_FFMPEG_LOGLEVEL=8
(cd backend && exec "../$PYTHON" -m app) &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true; wait "$BACKEND_PID" 2>/dev/null || true' EXIT

cd frontend
npm run dev -- --open
