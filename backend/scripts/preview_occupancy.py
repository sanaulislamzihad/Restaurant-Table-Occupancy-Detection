"""Show live table occupancy for a video's table config.

Usage (from the project folder, with the virtual environment active):
    python backend/scripts/preview_occupancy.py restaurant       # source from the config
    python backend/scripts/preview_occupancy.py restaurant --source fake_camera/videos/restaurant.mp4

Draws the tables (green = AVAILABLE, red = OCCUPIED, yellow = pending), the
people with their anchor points, and prints every status change. Draw the
tables first with draw_tables.py. Press q or Esc to quit.
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

from app.annotator import FrameAnnotator, put_label  # noqa: E402
from app.config_store import ConfigStore  # noqa: E402
from app.detector import PersonDetector  # noqa: E402
from app.occupancy import OccupancyTracker  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.sources import FrameSource  # noqa: E402

WINDOW = "Occupancy preview"


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Preview live table occupancy for a table config.")
    parser.add_argument("video_id", help="table config name (<CONFIGS_DIR>/<video_id>.json)")
    parser.add_argument("--source", help="override the source stored in the config")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)

    config = ConfigStore(settings.configs_dir).load(args.video_id)
    if config is None or not config.tables:
        sys.exit(f"No tables for '{args.video_id}'. Draw them first: "
                 f"python backend/scripts/draw_tables.py {args.video_id}")

    detector = PersonDetector.from_settings(settings, confidence=config.occupancy.confidence_threshold)
    annotator = FrameAnnotator()
    tracker: OccupancyTracker | None = None
    frame_size: tuple[int, int] | None = None
    tables = config.tables
    detections = sv.Detections.empty()
    last_frame: np.ndarray | None = None
    frame_count, processing_fps = 0, 0.0

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    with FrameSource.from_settings(args.source or config.source, settings) as source:
        while True:
            ok, frame = source.read(timeout=0.1)
            if ok and frame is not None:
                size = (frame.shape[1], frame.shape[0])
                if size != frame_size:  # first frame, or the video changed: scale the tables to it
                    frame_size = size
                    tables = config.tables_for_frame(*size)
                    tracker = OccupancyTracker(tables, config.occupancy)
                    detector.reset_tracking()
                last_frame = frame
                frame_count += 1
                if frame_count % settings.detect_every_n_frames == 0 and tracker is not None:
                    started = time.perf_counter()
                    detections = detector.detect(frame)
                    elapsed = time.perf_counter() - started
                    processing_fps = 0.9 * processing_fps + 0.1 / elapsed if processing_fps else 1 / elapsed
                    for event in tracker.update(detections):
                        name = next(t.name for t in tables if t.id == event.table_id)
                        clock = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
                        print(f"{clock} - {name} became {event.new_status.value}", flush=True)

            if last_frame is None or tracker is None:
                canvas = np.zeros((360, 640, 3), np.uint8)
                put_label(canvas, f"Source: {source.status.value}", (10, 40), (40, 40, 40), 0.7)
            else:
                canvas = annotator.annotate(
                    last_frame, detections, tables, tracker.states,
                    reference_point=config.occupancy.reference_point,
                    processing_fps=processing_fps,
                    source_status=source.status,
                )
                free = sum(not state.status.counts_as_occupied for state in tracker.states)
                put_label(canvas, f"{free} / {len(tables)} tables available", (6, canvas.shape[0] - 6),
                          (40, 40, 40), max(0.45, canvas.shape[0] / 900))
            cv2.imshow(WINDOW, canvas)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
