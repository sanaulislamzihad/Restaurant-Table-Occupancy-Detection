"""Show frames from any video source in a window.

The same FrameSource code handles a video file, an RTSP/HTTP stream and a
webcam, which is what this script is for checking.

Usage (from the project folder, with the virtual environment active):
    python backend/scripts/preview_source.py                   # FAKE_CAMERA_RTSP_URL from backend/.env
    python backend/scripts/preview_source.py rtsp://localhost:8554/cam1
    python backend/scripts/preview_source.py fake_camera/videos/restaurant.mp4
    python backend/scripts/preview_source.py 0                 # webcam 0

Press q or Esc (or close the window) to quit.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # makes the "app" package importable

from app.settings import get_settings  # noqa: E402
from app.sources import FrameSource, redact  # noqa: E402

WINDOW = "Source preview"
PLACEHOLDER_SHAPE = (360, 640, 3)  # shown until the first frame arrives


def draw_lines(image: np.ndarray, lines: list[str]) -> None:
    """Write text lines in the top-left corner, readable on any background."""
    y = 30
    for line in lines:
        cv2.putText(image, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(image, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        y += 30


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Preview a video file, stream URL or webcam index.")
    parser.add_argument(
        "source",
        nargs="?",
        default=settings.fake_camera_rtsp_url,
        help="file path, rtsp:// URL or webcam index (default: FAKE_CAMERA_RTSP_URL)",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)

    last_frame: np.ndarray | None = None
    shown, shown_fps, window_start = 0, 0.0, time.monotonic()
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    with FrameSource.from_settings(args.source, settings) as source:
        while True:
            ok, frame = source.read(timeout=0.1)
            if ok:
                last_frame = frame
                shown += 1
            now = time.monotonic()
            if now - window_start >= 1.0:
                shown_fps, shown, window_start = shown / (now - window_start), 0, now

            canvas = last_frame.copy() if last_frame is not None else np.zeros(PLACEHOLDER_SHAPE, np.uint8)
            size = source.resolution
            draw_lines(
                canvas,
                [
                    redact(source.source),
                    f"Status: {source.status.value.upper()}",
                    f"Source: {source.fps:.1f} fps   Shown: {shown_fps:.1f} fps",
                    f"Size: {size[0]}x{size[1]}" if size else "Size: -",
                ],
            )
            cv2.imshow(WINDOW, canvas)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
