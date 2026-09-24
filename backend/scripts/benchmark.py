"""Measure detection + tracking speed of one or more YOLO models on a video.

Usage (from the project folder, with the virtual environment active):
    python backend/scripts/benchmark.py fake_camera/videos/restaurant.mp4
    python backend/scripts/benchmark.py fake_camera/videos/restaurant.mp4 --models yolo11n.pt yolo26s.pt --frames 200

Every model runs on the same frames (read from the file up front, so disk and
decoding speed do not count) with the device, image size and confidence from
backend/.env. It prints frames per second and the average number of people
found per frame. Weights missing from MODELS_DIR are downloaded first.
"""

from __future__ import annotations

import argparse
import platform
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # makes the "app" package importable

from app.detector import PersonDetector  # noqa: E402
from app.settings import get_settings  # noqa: E402

WARMUP_FRAMES = 5  # not timed: the first calls include model setup


def read_frames(path: Path, count: int) -> list[np.ndarray]:
    """Up to ``count`` frames from the start of the video."""
    capture = cv2.VideoCapture(str(path))
    frames: list[np.ndarray] = []
    while len(frames) < count:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    return frames


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Measure YOLO person detection + tracking speed.")
    parser.add_argument("video", type=Path, help="video file to run on")
    parser.add_argument("--models", nargs="+", default=[settings.yolo_model],
                        help="weights file names in MODELS_DIR (default: YOLO_MODEL)")
    parser.add_argument("--frames", type=int, default=150, help="timed frames per model (default: 150)")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    frames = read_frames(args.video, args.frames + WARMUP_FRAMES)
    if len(frames) <= WARMUP_FRAMES:
        print(f"Could not read enough frames from {args.video}")
        return 1
    height, width = frames[0].shape[:2]
    print(f"{len(frames) - WARMUP_FRAMES} frames of {width}x{height}, image size {settings.yolo_img_size}, "
          f"confidence {settings.confidence_threshold}, {platform.processor() or platform.machine()}")
    print(f"{'model':<14}{'device':<9}{'fps':>7}{'ms/frame':>10}{'people/frame':>14}")

    for model in args.models:
        detector = PersonDetector(
            settings.models_dir / model,
            device=settings.device,
            confidence=settings.confidence_threshold,
            image_size=settings.yolo_img_size,
        )
        for frame in frames[:WARMUP_FRAMES]:
            detector.detect(frame)
        people = 0
        started = time.perf_counter()
        for frame in frames[WARMUP_FRAMES:]:
            people += len(detector.detect(frame))
        elapsed = time.perf_counter() - started
        timed = len(frames) - WARMUP_FRAMES
        print(f"{model:<14}{detector.device:<9}{timed / elapsed:>7.1f}{elapsed / timed * 1000:>10.0f}"
              f"{people / timed:>14.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
