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


def test_tracker_ids_stay_stable_while_people_barely_move(
    detector: PersonDetector, street_image: np.ndarray
) -> None:
    detector.reset_tracking()
    all_ids, confident_ids_per_frame = [], []
    for shift in range(12):  # the scene drifts 2 px per frame, like a slightly shaky camera
        detections = detector.detect(np.roll(street_image, shift * 2, axis=1))
        all_ids.append(set(detections.tracker_id.tolist()))
        confident_ids_per_frame.append(set(detections.tracker_id[detections.confidence >= 0.7].tolist()))
    first_frame_ids = all_ids[0]
    assert len(confident_ids_per_frame[0]) >= 3
    # Clearly visible people keep the same ID in every frame ...
    assert all(ids == confident_ids_per_frame[0] for ids in confident_ids_per_frame)
    # ... and nobody gets a new ID, even a borderline person who drops out for a frame.
    assert set().union(*all_ids) == first_frame_ids


def test_empty_frame_gives_empty_detections(detector: PersonDetector) -> None:
    detector.reset_tracking()
    detections = detector.detect(np.zeros((480, 640, 3), np.uint8))
    assert len(detections) == 0
    assert detections.tracker_id is not None and len(detections.tracker_id) == 0
