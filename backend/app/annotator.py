"""Draw table zones, people and a status overlay on video frames.

* Table polygons: green = AVAILABLE, red = OCCUPIED, yellow = pending states,
  with the table name and status above each polygon.
* People: box, tracker ID and (pink dot) the anchor point used for the table test.
* Corner overlay: time, processing FPS and source status.
"""

from __future__ import annotations

import time

import cv2
import numpy as np
import supervision as sv

from app.occupancy import TableState, TableStatus, anchor_position
from app.schemas import ReferencePoint, TableDef
from app.sources import SourceStatus

# BGR colours
GREEN = (60, 180, 75)
RED = (40, 40, 220)
YELLOW = (0, 200, 255)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
PINK = (255, 0, 255)  # people's reference points: stands out on light floors and tables

STATUS_COLORS = {
    TableStatus.AVAILABLE: GREEN,
    TableStatus.OCCUPIED: RED,
    TableStatus.PENDING_OCCUPIED: YELLOW,
    TableStatus.PENDING_AVAILABLE: YELLOW,
}
STATUS_TEXT = {
    TableStatus.AVAILABLE: "AVAILABLE",
    TableStatus.OCCUPIED: "OCCUPIED",
    TableStatus.PENDING_OCCUPIED: "PENDING",
    TableStatus.PENDING_AVAILABLE: "PENDING",
}


def source_label(status: SourceStatus) -> str:
    """What the overlay shows for a source status: LIVE, RECONNECTING or NO SOURCE."""
    if status is SourceStatus.LIVE:
        return "LIVE"
    if status in (SourceStatus.CONNECTING, SourceStatus.RECONNECTING):
        return "RECONNECTING"
    return "NO SOURCE"


def draw_reference_point(image: np.ndarray, point: tuple[int, int], radius: int = 5) -> None:
    """A person's reference point: a pink dot with a dark ring, visible on any background."""
    cv2.circle(image, point, radius + 2, BLACK, cv2.FILLED, cv2.LINE_AA)
    cv2.circle(image, point, radius, PINK, cv2.FILLED, cv2.LINE_AA)


def _text_scale(frame: np.ndarray) -> float:
    """Text size that stays readable on small and large frames."""
    return max(0.4, frame.shape[0] / 900)


def put_label(frame: np.ndarray, text: str, origin: tuple[int, int], color: tuple[int, int, int],
              scale: float, text_color: tuple[int, int, int] = WHITE) -> None:
    """Text on a filled box; origin is the bottom-left corner of the box."""
    thickness = max(1, round(scale * 2))
    (width, height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    x = min(max(origin[0], 0), max(frame.shape[1] - width - 6, 0))
    y = min(max(origin[1], height + baseline + 6), frame.shape[0])
    cv2.rectangle(frame, (x, y - height - baseline - 6), (x + width + 6, y), color, cv2.FILLED)
    cv2.putText(frame, text, (x + 3, y - baseline - 3), cv2.FONT_HERSHEY_SIMPLEX, scale, text_color,
                thickness, cv2.LINE_AA)


class FrameAnnotator:
    """Draws tables, people and the status overlay onto a copy of a frame."""

    def __init__(self, fill_alpha: float = 0.25) -> None:
        self.fill_alpha = fill_alpha

    def annotate(
        self,
        frame: np.ndarray,
        detections: sv.Detections,
        tables: list[TableDef],
        states: list[TableState],
        *,
        reference_point: ReferencePoint = "bottom_center",
        processing_fps: float = 0.0,
        source_status: SourceStatus = SourceStatus.LIVE,
    ) -> np.ndarray:
        image = frame.copy()
        self.draw_tables(image, tables, states)
        self.draw_people(image, detections, reference_point)
        self.draw_overlay(image, processing_fps, source_status)
        return image

    def draw_tables(self, image: np.ndarray, tables: list[TableDef], states: list[TableState]) -> None:
        by_id = {state.table_id: state for state in states}
        scale = _text_scale(image)
        fill = image.copy()
        outlines = []
        for table in tables:
            state = by_id.get(table.id)
            color = STATUS_COLORS[state.status] if state else WHITE
            points = np.rint(np.array(table.polygon)).astype(np.int32)
            cv2.fillPoly(fill, [points], color)
            outlines.append((table, state, points, color))
        cv2.addWeighted(fill, self.fill_alpha, image, 1 - self.fill_alpha, 0, dst=image)
        for table, state, points, color in outlines:
            cv2.polylines(image, [points], isClosed=True, color=color, thickness=2, lineType=cv2.LINE_AA)
            status = STATUS_TEXT[state.status] if state else "?"
            label = f"{table.name}: {status}" + (f" ({state.people_count})" if state and state.people_count else "")
            top = points[np.argmin(points[:, 1])]
            text_color = BLACK if color is YELLOW else WHITE
            put_label(image, label, (int(points[:, 0].min()), int(top[1]) - 4), color, scale, text_color)

    def draw_people(self, image: np.ndarray, detections: sv.Detections, reference_point: ReferencePoint) -> None:
        if len(detections) == 0:
            return
        scale = _text_scale(image) * 0.85
        anchors = detections.get_anchors_coordinates(anchor_position(reference_point))
        track_ids = detections.tracker_id if detections.tracker_id is not None else [-1] * len(detections)
        for (x1, y1, x2, y2), (ax, ay), track_id in zip(detections.xyxy.astype(int), anchors.astype(int), track_ids):
            cv2.rectangle(image, (x1, y1), (x2, y2), WHITE, 1, cv2.LINE_AA)
            draw_reference_point(image, (ax, ay), radius=4)
            if track_id >= 0:
                put_label(image, f"#{track_id}", (x1, y1), (80, 80, 80), scale)

    def draw_overlay(self, image: np.ndarray, processing_fps: float, source_status: SourceStatus) -> None:
        scale = _text_scale(image)
        label = source_label(source_status)
        color = GREEN if label == "LIVE" else (YELLOW if label == "RECONNECTING" else RED)
        line_height = int(cv2.getTextSize("Ag", cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][1] * 2.2)
        put_label(image, time.strftime("%Y-%m-%d %H:%M:%S"), (6, line_height), (40, 40, 40), scale)
        put_label(image, f"{processing_fps:.1f} FPS", (6, 2 * line_height + 2), (40, 40, 40), scale)
        put_label(image, label, (6, 3 * line_height + 4), color, scale, BLACK if color is YELLOW else WHITE)
