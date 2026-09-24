"""Show person detection and tracking on a live source.

Usage (from the project folder, with the virtual environment active):
    python backend/scripts/preview_detection.py                  # FAKE_CAMERA_RTSP_URL from backend/.env
    python backend/scripts/preview_detection.py fake_camera/videos/restaurant.mp4
    python backend/scripts/preview_detection.py 0 --conf 0.5

Each person gets a box labelled with its tracker ID and confidence, plus a
short trail. A seated person should keep the same ID. Press q or Esc to quit.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import supervision as sv
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # makes the "app" package importable

from app.detector import PersonDetector  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.sources import FrameSource  # noqa: E402
from preview_source import PLACEHOLDER_SHAPE, draw_lines  # noqa: E402

WINDOW = "Detection preview"


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Preview person detection and tracking on a source.")
    parser.add_argument("source", nargs="?", default=settings.fake_camera_rtsp_url,
                        help="file path, rtsp:// URL or webcam index (default: FAKE_CAMERA_RTSP_URL)")
    parser.add_argument("--conf", type=float, default=settings.confidence_threshold,
                        help="minimum person confidence (default: CONFIDENCE_THRESHOLD)")
    parser.add_argument("--every", type=int, default=settings.detect_every_n_frames,
                        help="run detection on every Nth frame (default: DETECT_EVERY_N_FRAMES)")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)

    detector = PersonDetector.from_settings(settings, confidence=args.conf)
    box_annotator = sv.BoxAnnotator(thickness=2)
    label_annotator = sv.LabelAnnotator(text_scale=0.5, text_padding=4)
    trace_annotator = sv.TraceAnnotator(trace_length=40)

    detections = sv.Detections.empty()
    shown: np.ndarray | None = None
    frame_count = 0
    processing_fps = 0.0
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    with FrameSource.from_settings(args.source, settings) as source:
        while True:
            ok, frame = source.read(timeout=0.1)
            if ok and frame is not None:
                frame_count += 1
                if frame_count % max(args.every, 1) == 0:
                    started = time.perf_counter()
                    detections = detector.detect(frame)
                    elapsed = time.perf_counter() - started
                    # Smoothed so the number is readable.
                    processing_fps = 0.9 * processing_fps + 0.1 / elapsed if processing_fps else 1 / elapsed

                labels = [f"#{tid} {conf:.2f}" for tid, conf in zip(detections.tracker_id, detections.confidence)]
                shown = trace_annotator.annotate(frame.copy(), detections)
                shown = box_annotator.annotate(shown, detections)
                shown = label_annotator.annotate(shown, detections, labels=labels)
                draw_lines(
                    shown,
                    [
                        f"Status: {source.status.value.upper()}",
                        f"People: {len(detections)}",
                        f"Detection: {processing_fps:.1f} fps on {detector.device}",
                    ],
                )

            canvas = shown if shown is not None else np.zeros(PLACEHOLDER_SHAPE, np.uint8)
            if shown is None:
                draw_lines(canvas, [f"Status: {source.status.value.upper()}"])
            cv2.imshow(WINDOW, canvas)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
