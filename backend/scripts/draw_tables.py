"""Draw table outlines on a camera frame and save them as a table config.

Usage (from the project folder, with the virtual environment active):
    python backend/scripts/draw_tables.py restaurant             # frame from FAKE_CAMERA_RTSP_URL
    python backend/scripts/draw_tables.py restaurant --source fake_camera/videos/restaurant.mp4
    python backend/scripts/draw_tables.py lobby --source rtsp://user:pass@192.168.1.20/stream

The config is saved as <CONFIGS_DIR>/<video_id>.json. If it already exists, its
tables are loaded for editing and its occupancy settings are kept.

Controls:
    left click            add a corner to the current table
    right click / Enter   finish the current table (needs 3+ corners)
    Backspace             undo the last corner (or remove the last table)
    c                     clear all tables
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

from app.annotator import BLACK, YELLOW, put_label  # noqa: E402
from app.config_store import ConfigStore, default_occupancy  # noqa: E402
from app.schemas import TableConfig, TableDef  # noqa: E402
from app.settings import Settings, get_settings  # noqa: E402
from app.sources import FrameSource, redact  # noqa: E402

WINDOW = "Draw tables"
MAX_DISPLAY_SIZE = (1280, 720)  # small CCTV frames are enlarged to this, big ones shrunk
TABLE_COLOR = (255, 200, 0)
HELP = "Left click: corner | Right click/Enter: finish table | Backspace: undo | c: clear | s: save | q: quit"
KEY_ENTER, KEY_BACKSPACE, KEY_ESC = 13, 8, 27


def grab_frame(source: str, settings: Settings) -> np.ndarray:
    """Read one frame from the source."""
    logger.info("Reading a frame from {}", redact(source))
    with FrameSource.from_settings(source, settings) as frame_source:
        ok, frame = frame_source.read(timeout=settings.source_open_timeout_seconds + 10)
    if not ok or frame is None:
        sys.exit(f"Could not read a frame from {redact(source)}. Is the camera running?")
    return frame


class TableEditor:
    """Mouse and keyboard polygon editing on one frame (all coordinates in frame pixels)."""

    def __init__(self, frame: np.ndarray, tables: list[TableDef]) -> None:
        self.frame = frame
        height, width = frame.shape[:2]
        self.scale = min(MAX_DISPLAY_SIZE[0] / width, MAX_DISPLAY_SIZE[1] / height)
        self.tables = list(tables)
        self.points: list[tuple[int, int]] = []

    def on_mouse(self, event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((round(x / self.scale), round(y / self.scale)))
        elif event == cv2.EVENT_RBUTTONDOWN:
            self.finish_table()

    def finish_table(self) -> None:
        if len(self.points) < 3:
            return
        used = {table.id for table in self.tables}
        number = next(n for n in itertools.count(1) if f"T{n}" not in used)
        self.tables.append(TableDef(id=f"T{number}", name=f"Table {number}", polygon=self.points))
        self.points = []

    def undo(self) -> None:
        if self.points:
            self.points.pop()
        elif self.tables:
            self.tables.pop()

    def render(self) -> np.ndarray:
        image = cv2.resize(self.frame, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_LINEAR)
        for table in self.tables:
            points = np.rint(np.array(table.polygon) * self.scale).astype(np.int32)
            cv2.polylines(image, [points], isClosed=True, color=TABLE_COLOR, thickness=2, lineType=cv2.LINE_AA)
            top = points[np.argmin(points[:, 1])]
            put_label(image, table.name, (int(points[:, 0].min()), int(top[1]) - 4), TABLE_COLOR, 0.6, BLACK)
        current = [(round(x * self.scale), round(y * self.scale)) for x, y in self.points]
        for start, end in zip(current, current[1:]):
            cv2.line(image, start, end, YELLOW, 2, cv2.LINE_AA)
        for point in current:
            cv2.circle(image, point, 5, YELLOW, cv2.FILLED)
        put_label(image, HELP, (6, image.shape[0] - 6), (40, 40, 40), 0.5)
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
    source = args.source or (existing.source if existing else settings.fake_camera_rtsp_url)
    if Path(source).is_file():
        source = str(Path(source).resolve())  # keep working from any folder
    frame = grab_frame(source, settings)
    height, width = frame.shape[:2]

    editor = TableEditor(frame, existing.tables_for_frame(width, height) if existing else [])
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
        elif key == ord("s"):
            editor.finish_table()
            saved = True
            break
        elif key in (ord("q"), KEY_ESC) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()

    if not saved:
        print("Not saved.")
        return 1
    tables = [
        table.model_copy(update={"polygon": [(round(x), round(y)) for x, y in table.polygon]})
        for table in editor.tables
    ]
    config = TableConfig(
        video_id=args.video_id,
        source=source,
        frame_width=width,
        frame_height=height,
        tables=tables,
        occupancy=existing.occupancy if existing else default_occupancy(settings),
    )
    path = store.save(config)
    print(f"Saved {len(tables)} table(s) to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
