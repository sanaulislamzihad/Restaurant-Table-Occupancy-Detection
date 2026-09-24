"""Person detection and tracking with Ultralytics YOLO and its built-in ByteTrack.

The model is loaded once. ``detect()`` takes one BGR frame and returns the
people in it as ``supervision.Detections``, each with a tracker ID that stays
the same for the same person from frame to frame.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import supervision as sv
import torch
from loguru import logger
from ultralytics import YOLO

if TYPE_CHECKING:
    from app.settings import Settings

PERSON_CLASS_ID = 0  # "person" in the COCO classes the official YOLO weights use


def select_device(requested: str) -> str:
    """Resolve DEVICE: "auto" means the first CUDA GPU if there is one, else the CPU."""
    requested = requested.strip().lower()
    if requested != "auto":
        return requested
    return "cuda:0" if torch.cuda.is_available() else "cpu"


class PersonDetector:
    """YOLO person detector with ByteTrack tracking across frames."""

    def __init__(
        self,
        model_path: Path | str,
        *,
        device: str = "auto",
        confidence: float = 0.4,
        image_size: int = 640,
        tracker_config: str = "bytetrack.yaml",
    ) -> None:
        self.device = select_device(device)
        self.confidence = confidence
        self.image_size = image_size
        self._tracker_config = tracker_config
        # FP16 is faster on NVIDIA GPUs; the CPU stays at full precision.
        self._precision: dict[str, int] = {"quantize": 16} if self.device.startswith("cuda") else {}

        model_path = Path(model_path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Loading {} on {}", model_path.name, self.device)
        self._model = YOLO(str(model_path))  # official weights are downloaded on first use

        # The first inference is slow (model setup); do it now instead of on the first live frame.
        self.detect(np.zeros((image_size, image_size, 3), np.uint8))
        self.reset_tracking()

    @classmethod
    def from_settings(cls, settings: Settings, *, confidence: float | None = None) -> PersonDetector:
        """Create a detector from the settings (optionally with another confidence)."""
        return cls(
            settings.yolo_model_path,
            device=settings.device,
            confidence=settings.confidence_threshold if confidence is None else confidence,
            image_size=settings.yolo_img_size,
        )

    def detect(self, frame: np.ndarray) -> sv.Detections:
        """Detect and track the people in one BGR frame.

        The result always has ``tracker_id`` set (an empty array when nobody is
        found). A tracker ID of -1 means the tracker has not confirmed that
        person yet, which can happen for a frame or two when someone appears.
        """
        results = self._model.track(
            frame,
            persist=True,  # keep the tracker state between calls
            tracker=self._tracker_config,
            classes=[PERSON_CLASS_ID],
            conf=self.confidence,
            imgsz=self.image_size,
            device=self.device,
            verbose=False,
            **self._precision,
        )
        detections = sv.Detections.from_ultralytics(results[0])
        if detections.tracker_id is None:
            detections.tracker_id = np.full(len(detections), -1, dtype=int)
        return detections

    def reset_tracking(self) -> None:
        """Forget all tracks, e.g. when the video source changes."""
        predictor = self._model.predictor
        for tracker in getattr(predictor, "trackers", None) or []:
            tracker.reset()
