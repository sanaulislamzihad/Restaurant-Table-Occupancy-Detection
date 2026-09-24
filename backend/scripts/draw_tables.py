"""Draw table outlines on a camera frame and save them as a table config.

Usage (from the project folder, with the virtual environment active):
    python backend/scripts/draw_tables.py restaurant             # frame from FAKE_CAMERA_RTSP_URL
    python backend/scripts/draw_tables.py restaurant --source fake_camera/videos/restaurant.mp4
    python backend/scripts/draw_tables.py lobby --source rtsp://user:pass@192.168.1.20/stream

The config is saved as <CONFIGS_DIR>/<video_id>.json. If it already exists, its
tables are loaded for editing and its occupancy settings are kept.

The people found in the frame are marked with white dots: the point that has
to be inside a table outline for that table to count the person. Draw each
outline around the table and its chairs so the dots of the seated people fall
inside it.

Controls:
    left click            add a corner to the current table (click around the table,
                          e.g. its 4 outer corners including the chairs)
    right click / Enter   finish the current table
    Backspace             undo the last corner (or remove the last table)
    c                     clear all tables
    h                     show / hide the help text
    s                     save and quit
    q / Esc               quit without saving
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # makes the "app" package importable

from app.annotator import BLACK, RED, WHITE, YELLOW, put_label  # noqa: E402
from app.config_store import ConfigStore, default_occupancy  # noqa: E402
from app.detector import PersonDetector  # noqa: E402
from app.geometry import outline_problem, tidy_polygon  # noqa: E402
from app.occupancy import anchor_position  # noqa: E402
from app.schemas import OccupancySettings, TableConfig, TableDef  # noqa: E402
from app.settings import Settings, get_settings  # noqa: E402
from app.sources import FrameSource, redact  # noqa: E402

WINDOW = "Draw tables"
MAX_DISPLAY_SIZE = (1280, 720)  # small CCTV frames are enlarged to this, big ones shrunk
TABLE_COLOR = (255, 200, 0)
HELP_LINES = [
    "Click the corners around a table and its chairs (4 is usually enough), then right-click or Enter.",
    "White dots = people's reference points. They must be inside a table's outline to count.",
    "Backspace: undo | c: clear all | s: save | q: quit | h: hide this help",
]
KEY_ENTER, KEY_BACKSPACE, KEY_ESC = 13, 8, 27


def grab_frame(source: str, settings: Settings) -> np.ndarray:
    """Read one frame from the source."""
    logger.info("Reading a frame from {}", redact(source))
    with FrameSource.from_settings(source, settings) as frame_source:
        ok, frame = frame_source.read(timeout=settings.source_open_timeout_seconds + 10)
    if not ok or frame is None:
        sys.exit(f"Could not read a frame from {redact(source)}. Is the camera running?")
    return frame


def people_anchor_points(frame: np.ndarray, settings: Settings, occupancy: OccupancySettings) -> np.ndarray:
    """Reference points of the people in the frame (empty if detection is unavailable)."""
    try:
        detector = PersonDetector.from_settings(settings, confidence=occupancy.confidence_threshold)
        detections = detector.detect(frame)
    except Exception as error:  # drawing still works without the hints
        logger.warning("Could not detect people for the hints: {}", error)
        return np.empty((0, 2))
    return detections.get_anchors_coordinates(anchor_position(occupancy.reference_point))


class TableEditor:
    """Mouse and keyboard polygon editing on one frame (all coordinates in frame pixels)."""

    def __init__(self, frame: np.ndarray, tables: list[TableDef], people: np.ndarray) -> None:
        self.frame = frame
        height, width = frame.shape[:2]
        self.frame_size = (width, height)
        self.scale = min(MAX_DISPLAY_SIZE[0] / width, MAX_DISPLAY_SIZE[1] / height)
        self.tables = list(tables)
        self.people = people
        self.points: list[tuple[float, float]] = []
        self.message = "Draw the first table."
        self.message_is_error = False
        self.show_help = True

    def on_mouse(self, event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((round(x / self.scale), round(y / self.scale)))
        elif event == cv2.EVENT_RBUTTONDOWN:
            self.finish_table()

    def finish_table(self) -> None:
        if not self.points:
            return
        points = tidy_polygon(self.points)  # fixes corners clicked in a criss-cross order
        problem = outline_problem(points, self.frame_size)
        if problem:
            self.message, self.message_is_error = f"Not added: {problem}. Undo with Backspace and try again.", True
            return
        used = {table.id for table in self.tables}
        number = next(n for n in itertools.count(1) if f"T{n}" not in used)
        table = TableDef(id=f"T{number}", name=f"Table {number}", polygon=[(round(x), round(y)) for x, y in points])
        self.tables.append(table)
        self.points = []
        self.message, self.message_is_error = f"{table.name} added ({len(self.tables)} in total).", False

    def undo(self) -> None:
        if self.points:
            self.points.pop()
        elif self.tables:
            removed = self.tables.pop()
            self.message, self.message_is_error = f"{removed.name} removed.", False

    def _to_display(self, points: list[tuple[float, float]]) -> np.ndarray:
        return np.rint(np.array(points, dtype=float) * self.scale).astype(np.int32)

    def render(self) -> np.ndarray:
        image = cv2.resize(self.frame, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_LINEAR)
        fill = image.copy()
        shapes = [(self._to_display(t.polygon), TABLE_COLOR) for t in self.tables]
        if len(self.points) >= 3:
            shapes.append((self._to_display(self.points), YELLOW))
        for points, color in shapes:
            cv2.fillPoly(fill, [points], color)
        cv2.addWeighted(fill, 0.3, image, 0.7, 0, dst=image)
        for table in self.tables:
            points = self._to_display(table.polygon)
            cv2.polylines(image, [points], isClosed=True, color=TABLE_COLOR, thickness=2, lineType=cv2.LINE_AA)
            top = points[np.argmin(points[:, 1])]
            put_label(image, table.name, (int(points[:, 0].min()), int(top[1]) - 4), TABLE_COLOR, 0.55, BLACK)

        if self.points:  # the table being drawn, shown closed so its shape is obvious
            current = self._to_display(self.points)
            cv2.polylines(image, [current], isClosed=len(current) >= 3, color=YELLOW, thickness=2,
                          lineType=cv2.LINE_AA)
            for point in current:
                cv2.circle(image, tuple(int(v) for v in point), 5, YELLOW, cv2.FILLED)

        for x, y in self.people * self.scale:
            cv2.circle(image, (int(x), int(y)), 6, BLACK, cv2.FILLED)
            cv2.circle(image, (int(x), int(y)), 4, WHITE, cv2.FILLED)

        lines = HELP_LINES if self.show_help else []
        for i, line in enumerate(lines):
            put_label(image, line, (6, 26 + i * 26), (40, 40, 40), 0.5)
        put_label(image, self.message, (6, 32 + len(lines) * 26), RED if self.message_is_error else (40, 40, 40), 0.6)
        return image


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Draw table polygons and save them as a table config.")
    parser.add_argument("video_id", help="config name, saved as <CONFIGS_DIR>/<video_id>.json")
    parser.add_argument("--source", help="file, rtsp:// URL or webcam index (default: the config's "
                                         "source, else FAKE_CAMERA_RTSP_URL)")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)

    store = ConfigStore(settings.configs_dir)
    existing = store.load(args.video_id)
    occupancy = existing.occupancy if existing else default_occupancy(settings)
    source = args.source or (existing.source if existing else settings.fake_camera_rtsp_url)
    if Path(source).is_file():
        source = str(Path(source).resolve())  # keep working from any folder
    frame = grab_frame(source, settings)
    height, width = frame.shape[:2]
    people = people_anchor_points(frame, settings, occupancy)

    editor = TableEditor(frame, existing.tables_for_frame(width, height) if existing else [], people)
    if existing:
        bad = [t.name for t in editor.tables if outline_problem(t.polygon, (width, height))]
        if bad:
            editor.message = f"Check these outlines (too thin or crossed): {', '.join(bad)}. Press c to start over."
            editor.message_is_error = True
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WINDOW, editor.on_mouse)
    saved = False
    while True:
        cv2.imshow(WINDOW, editor.render())
        key = cv2.waitKey(20) & 0xFF
        if key == KEY_ENTER:
            editor.finish_table()
        elif key == KEY_BACKSPACE:
            editor.undo()
        elif key == ord("c"):
            editor.tables, editor.points = [], []
            editor.message, editor.message_is_error = "All tables cleared.", False
        elif key == ord("h"):
            editor.show_help = not editor.show_help
        elif key == ord("s"):
            editor.finish_table()
            if not editor.points:  # an unfinished bad outline blocks saving so it is not lost silently
                saved = True
                break
        elif key in (ord("q"), KEY_ESC) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()

    if not saved:
        print("Not saved.")
        return 1
    config = TableConfig(
        video_id=args.video_id,
        source=source,
        frame_width=width,
        frame_height=height,
        tables=[
            table.model_copy(update={"polygon": [(round(x), round(y)) for x, y in table.polygon]})
            for table in editor.tables
        ],
        occupancy=occupancy,
    )
    path = store.save(config)
    print(f"Saved {len(config.tables)} table(s) to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
