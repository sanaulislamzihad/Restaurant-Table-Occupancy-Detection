"""Help for the table editor: people's reference points and suggested table outlines in one frame.

Uses its own YOLO instance, so analysing a still frame never disturbs the
tracker of the live pipeline.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import numpy as np
import supervision as sv
from loguru import logger
from ultralytics import YOLO

from app.detector import PERSON_CLASS_ID, select_device
from app.geometry import Point, suggest_table_outlines
from app.occupancy import anchor_position

if TYPE_CHECKING:
    from app.settings import Settings

CHAIR_CLASS_ID = 56  # COCO "chair"
DINING_TABLE_CLASS_ID = 60  # COCO "dining table"
# Tables are hard to recognise from above; every suggestion is checked by the
# user anyway, so weak detections are welcome.
TABLE_CONFIDENCE = 0.1
CHAIR_CONFIDENCE = 0.2


class SceneHints(Protocol):
    """What the table editor needs (SceneAnalyzer, or a fake in tests)."""

    def analyze(
        self, frame: np.ndarray, *, person_confidence: float, reference_point: str
    ) -> tuple[list[Point], list[list[Point]]]: ...


class SceneAnalyzer:
    """Finds people, chairs and tables in a still frame."""

    def __init__(self, model_path: Path | str, *, device: str = "auto", image_size: int = 640) -> None:
        self.device = select_device(device)
        self.image_size = image_size
        self._precision: dict[str, int] = {"quantize": 16} if self.device.startswith("cuda") else {}
        self._lock = threading.Lock()
        model_path = Path(model_path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Loading {} for the table editor", model_path.name)
        self._model = YOLO(str(model_path))  # official weights are downloaded on first use

    @classmethod
    def from_settings(cls, settings: Settings) -> SceneAnalyzer:
        return cls(settings.yolo_model_path, device=settings.device, image_size=settings.yolo_img_size)

    def analyze(
        self, frame: np.ndarray, *, person_confidence: float, reference_point: str
    ) -> tuple[list[Point], list[list[Point]]]:
        """(people's reference points, suggested table outlines) in frame pixels."""
        with self._lock:  # one YOLO call at a time on this model
            result = self._model.predict(
                frame,
                classes=[PERSON_CLASS_ID, CHAIR_CLASS_ID, DINING_TABLE_CLASS_ID],
                conf=min(person_confidence, TABLE_CONFIDENCE),
                imgsz=self.image_size,
                device=self.device,
                verbose=False,
                **self._precision,
            )[0]
        detections = sv.Detections.from_ultralytics(result)
        class_id, confidence = detections.class_id, detections.confidence
        people = detections[(class_id == PERSON_CLASS_ID) & (confidence >= person_confidence)]
        chairs = detections[(class_id == CHAIR_CLASS_ID) & (confidence >= CHAIR_CONFIDENCE)]
        tables = detections[class_id == DINING_TABLE_CLASS_ID]

        points = people.get_anchors_coordinates(anchor_position(reference_point)) if len(people) else np.empty((0, 2))
        height, width = frame.shape[:2]
        outlines = suggest_table_outlines(tables.xyxy, tables.confidence, chairs.xyxy, points, (width, height))
        return [(round(float(x)), round(float(y))) for x, y in points], outlines
