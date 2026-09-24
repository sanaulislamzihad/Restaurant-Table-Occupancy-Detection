"""End-to-end tests of the API and live pipeline, with a fake detector and a generated video."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import supervision as sv
from fastapi.testclient import TestClient

from app.config_store import ConfigStore
from app.main import create_app, mjpeg_parts
from app.schemas import OccupancySettings, TableConfig, TableDef
from app.settings import BACKEND_DIR, Settings

WIDTH, HEIGHT = 320, 180
TABLE_1 = TableDef(id="T1", name="Table 1", polygon=[(20, 20), (150, 20), (150, 170), (20, 170)])
TABLE_2 = TableDef(id="T2", name="Table 2", polygon=[(180, 20), (300, 20), (300, 170), (180, 170)])


class FakeDetector:
    """Always sees one person whose feet are inside Table 1."""

    device = "cpu"
    confidence = 0.1

    def detect(self, frame: np.ndarray) -> sv.Detections:
        return sv.Detections(
            xyxy=np.array([[60.0, 60.0, 100.0, 150.0]]),
            confidence=np.array([0.9]),
            class_id=np.array([0]),
            tracker_id=np.array([1]),
        )

    def reset_tracking(self) -> None:
        pass


def wait_until(condition: Callable[[], Any], timeout: float = 15.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    video = tmp_path / "demo.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 20, (WIDTH, HEIGHT))
    for i in range(20):
        writer.write(np.full((HEIGHT, WIDTH, 3), 40 + i * 5, np.uint8))
    writer.release()

    settings = Settings(_env_file=BACKEND_DIR / ".env.example").model_copy(  # type: ignore[call-arg]
        update={
            "configs_dir": tmp_path / "configs",
            "data_dir": tmp_path / "data",
            "default_video_id": "demo",
            "fake_camera_rtsp_url": "rtsp://127.0.0.1:9/none",
            "loop_video_files": True,
            "detect_every_n_frames": 1,
        }
    )
    ConfigStore(settings.configs_dir).save(TableConfig(
        video_id="demo",
        source=str(video),
        frame_width=WIDTH,
        frame_height=HEIGHT,
        tables=[TABLE_1],
        occupancy=OccupancySettings(confidence_threshold=0.1, enter_seconds=0, leave_seconds=10),
    ))
    app = create_app(settings, detector_factory=lambda _settings: FakeDetector())
    with TestClient(app) as test_client:
        wait_until(lambda: test_client.get("/api/health").json()["source"] == "LIVE")
        yield test_client


def table_status(client: TestClient) -> dict[str, str]:
    return {table["id"]: table["status"] for table in client.get("/api/tables").json()}


def test_health(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["pipeline_running"] and health["model"] == "ready"
    assert health["video_id"] == "demo" and health["device"] == "cpu"
    assert health["mediamtx"] == "not running"


def test_tables_become_occupied_and_events_are_stored(client: TestClient) -> None:
    wait_until(lambda: table_status(client).get("T1") == "OCCUPIED")
    table = client.get("/api/tables").json()[0]
    assert table["occupied"] and table["people_count"] == 1 and table["track_ids"] == [1]
    assert table["session_count"] == 1 and table["occupied_since"] is not None

    events = client.get("/api/events", params={"limit": 10}).json()
    assert [(e["old_status"], e["new_status"]) for e in reversed(events)] == [
        ("AVAILABLE", "PENDING_OCCUPIED"),
        ("PENDING_OCCUPIED", "OCCUPIED"),
    ]
    assert events[0]["table_name"] == "Table 1" and events[0]["video_id"] == "demo"
    assert client.get("/api/events", params={"limit": 0}).status_code == 422


def test_snapshot_is_a_raw_jpeg(client: TestClient) -> None:
    response = client.get("/api/snapshot")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (HEIGHT, WIDTH, 3)


def test_config_and_live_table_reload(client: TestClient, tmp_path: Path) -> None:
    config = client.get("/api/config").json()
    assert config["video_id"] == "demo" and [t["id"] for t in config["tables"]] == ["T1"]
    wait_until(lambda: table_status(client).get("T1") == "OCCUPIED")

    body = {"tables": [TABLE_1.model_dump(), TABLE_2.model_dump()]}
    response = client.put("/api/config/tables", json=body)
    assert response.status_code == 200, response.text
    assert [t["id"] for t in response.json()["tables"]] == ["T1", "T2"]
    assert response.json()["frame_width"] == WIDTH  # taken from the live frame

    statuses = wait_until(lambda: (s := table_status(client)) and "T2" in s and s)
    assert statuses == {"T1": "OCCUPIED", "T2": "AVAILABLE"}  # T1 kept its state
    saved = ConfigStore(tmp_path / "configs").load("demo")
    assert saved is not None and len(saved.tables) == 2


def test_unusable_outline_is_rejected(client: TestClient) -> None:
    line = {"id": "T9", "name": "Line", "polygon": [[10, 10], [100, 12], [200, 14]]}
    response = client.put("/api/config/tables", json={"tables": [line]})
    assert response.status_code == 422
    assert "Line" in response.json()["detail"]


def test_websocket_pushes_status(client: TestClient) -> None:
    with client.websocket_connect("/ws/status") as websocket:
        first = websocket.receive_json()
        assert first["type"] == "status"
        assert first["data"]["video_id"] == "demo"
        pushed = websocket.receive_json()  # change or heartbeat within about a second
        assert pushed["type"] in {"status", "event"}


def test_mjpeg_stream_parts(client: TestClient) -> None:
    pipeline = client.app.state.pipeline  # type: ignore[attr-defined]

    async def first_part() -> bytes:
        parts = mjpeg_parts(pipeline)
        try:
            return await parts.__anext__()
        finally:
            await parts.aclose()

    part = asyncio.run(first_part())
    assert part.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n")
    assert b"\r\n\r\n\xff\xd8" in part  # JPEG data follows the part headers
