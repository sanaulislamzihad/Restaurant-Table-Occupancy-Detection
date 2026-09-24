"""Tests for PersonDetector (uses the model from backend/.env; downloaded on first run)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
import torch
from ultralytics.utils import ASSETS

from app.detector import PERSON_CLASS_ID, PersonDetector, select_device
from app.settings import get_settings


@pytest.fixture(scope="module")
def detector() -> PersonDetector:
    return PersonDetector.from_settings(get_settings(), confidence=0.4)


@pytest.fixture(scope="module")
def street_image() -> np.ndarray:
    """Sample photo shipped with Ultralytics: a bus with several people in front of it."""
    image = cv2.imread(str(ASSETS / "bus.jpg"))
    assert image is not None
    return image


def test_select_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert select_device("auto") == "cpu"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert select_device("auto") == "cuda:0"
    assert select_device(" CPU ") == "cpu"
    assert select_device("cuda:1") == "cuda:1"


def test_detects_only_people(detector: PersonDetector, street_image: np.ndarray) -> None:
    detector.reset_tracking()
    detections = detector.detect(street_image)
    assert len(detections) >= 3
    assert set(detections.class_id.tolist()) == {PERSON_CLASS_ID}
    assert (detections.confidence >= 0.4).all()
    assert detections.tracker_id is not None and len(detections.tracker_id) == len(detections)


def shift_right(image: np.ndarray, dx: int) -> np.ndarray:
    """Move the whole scene dx pixels to the right (the uncovered strip is black)."""
    matrix = np.float32([[1, 0, dx], [0, 1, 0]])
    return cv2.warpAffine(image, matrix, (image.shape[1], image.shape[0]))


def test_each_person_keeps_their_tracker_id_while_barely_moving(
    detector: PersonDetector, street_image: np.ndarray
) -> None:
    width = street_image.shape[1]
    detector.reset_tracking()
    first = detector.detect(street_image)
    # Follow people who are clearly visible and not cut off by the image edge.
    people = [
        (track_id, box)
        for track_id, box, confidence in zip(first.tracker_id, first.xyxy, first.confidence)
        if confidence >= 0.5 and box[0] > 30 and box[2] < width - 30
    ]
    assert len(people) >= 2

    for step in range(1, 12):  # the scene drifts 2 px per frame, like a slightly shaky camera
        dx = 2 * step
        detections = detector.detect(shift_right(street_image, dx))
        centers = (detections.xyxy[:, :2] + detections.xyxy[:, 2:]) / 2
        for track_id, box in people:
            expected_center = (box[:2] + box[2:]) / 2 + np.array([dx, 0])
            nearest = int(np.argmin(np.linalg.norm(centers - expected_center, axis=1)))
            assert detections.tracker_id[nearest] == track_id


def test_empty_frame_gives_empty_detections(detector: PersonDetector) -> None:
    detector.reset_tracking()
    detections = detector.detect(np.zeros((480, 640, 3), np.uint8))
    assert len(detections) == 0
    assert detections.tracker_id is not None and len(detections.tracker_id) == 0
