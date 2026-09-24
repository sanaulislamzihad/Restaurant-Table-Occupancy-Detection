"""Tests for saving and loading table configs, and for drawing them."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import supervision as sv
from pydantic import ValidationError

from app.annotator import GREEN, FrameAnnotator
from app.config_store import ConfigStore, default_occupancy
from app.occupancy import OccupancyTracker
from app.schemas import OccupancySettings, TableConfig, TableDef
from app.settings import BACKEND_DIR, Settings

OCCUPANCY = OccupancySettings(confidence_threshold=0.4, enter_seconds=5, leave_seconds=10)


def make_config(video_id: str = "abc123", **overrides: object) -> TableConfig:
    values: dict[str, object] = {
        "video_id": video_id,
        "source": "rtsp://localhost:8554/cam1",
        "frame_width": 1280,
        "frame_height": 720,
        "tables": [
            TableDef(id="T1", name="Table 1", polygon=[(100, 300), (260, 300), (260, 430), (100, 430)]),
            TableDef(id="T2", name="Table 2", polygon=[(400, 280), (560, 280), (560, 410), (400, 410)]),
        ],
        "occupancy": OCCUPANCY,
    }
    values.update(overrides)
    return TableConfig(**values)


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    config = make_config()
    path = store.save(config)
    assert path == tmp_path / "abc123.json"
    assert store.load("abc123") == config
    assert store.exists("abc123") and store.list_ids() == ["abc123"]
    assert not list(tmp_path.glob("*.tmp"))  # the temporary file was renamed


def test_missing_config_loads_as_none(tmp_path: Path) -> None:
    assert ConfigStore(tmp_path).load("nothing") is None


def test_delete(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    store.save(make_config())
    assert store.delete("abc123") is True
    assert store.delete("abc123") is False
    assert store.list_ids() == []


def test_spec_example_json_loads(tmp_path: Path) -> None:
    (tmp_path / "abc123.json").write_text(
        """{
          "video_id": "abc123",
          "source": "rtsp://localhost:8554/cam1",
          "frame_width": 1280,
          "frame_height": 720,
          "tables": [
            { "id": "T1", "name": "Table 1", "polygon": [[100,300],[260,300],[260,430],[100,430]] }
          ],
          "occupancy": {"confidence_threshold": 0.4, "enter_seconds": 5, "leave_seconds": 10,
                        "reference_point": "bottom_center"}
        }""",
        encoding="utf-8",
    )
    config = ConfigStore(tmp_path).load("abc123")
    assert config is not None
    assert config.tables[0].polygon[1] == (260, 300)
    assert config.occupancy.presence_hold_seconds == 1.0  # optional field gets its default


@pytest.mark.parametrize("video_id", ["../evil", "a/b", "", "name with spaces", "x" * 65])
def test_unsafe_video_ids_are_rejected(tmp_path: Path, video_id: str) -> None:
    with pytest.raises(ValueError):
        ConfigStore(tmp_path).path(video_id)


def test_duplicate_table_ids_are_rejected() -> None:
    table = TableDef(id="T1", name="Table 1", polygon=[(0, 0), (1, 0), (1, 1)])
    with pytest.raises(ValidationError):
        make_config(tables=[table, table])


def test_polygon_needs_three_points() -> None:
    with pytest.raises(ValidationError):
        TableDef(id="T1", name="Table 1", polygon=[(0, 0), (1, 1)])


def test_default_occupancy_comes_from_env_template() -> None:
    settings = Settings(_env_file=BACKEND_DIR / ".env.example")  # type: ignore[call-arg]
    occupancy = default_occupancy(settings)
    assert occupancy.enter_seconds == settings.enter_seconds
    assert occupancy.leave_seconds == settings.leave_seconds
    assert occupancy.reference_point == settings.reference_point
    assert occupancy.confidence_threshold == settings.confidence_threshold


def test_annotator_draws_table_colours_without_touching_the_input() -> None:
    config = make_config()
    tables = config.tables_for_frame(640, 360)
    tracker = OccupancyTracker(tables, config.occupancy)
    frame = np.zeros((360, 640, 3), np.uint8)
    detections = sv.Detections(xyxy=np.array([[60.0, 150.0, 80.0, 200.0]]), tracker_id=np.array([3]))
    image = FrameAnnotator().annotate(frame, detections, tables, tracker.states, processing_fps=5.0)
    assert image.shape == frame.shape and not frame.any()  # input frame left untouched
    # The outline of Table 1 (x from 50 to 130 in the scaled frame) is drawn in green.
    x, y = 90, int(430 / 2)
    assert tuple(int(v) for v in image[y, x]) == GREEN
