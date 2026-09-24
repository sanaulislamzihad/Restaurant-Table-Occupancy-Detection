"""End-to-end tests of the live API with a fake detector, reading a video file configured as a camera."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config_store import ConfigStore
from app.main import create_app, mjpeg_parts
from app.schemas import OccupancySettings, TableConfig, TableDef
from conftest import FakeDetector, FakePopen, make_settings, write_video

WIDTH, HEIGHT = 320, 180
TABLE_1 = TableDef(id="T1", name="Table 1", polygon=[(20, 20), (150, 20), (150, 170), (20, 170)])
TABLE_2 = TableDef(id="T2", name="Table 2", polygon=[(180, 20), (300, 20), (300, 170), (180, 170)])


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
    """The "demo" config reads a video file directly, the way a real camera config would."""
    video = write_video(tmp_path / "camera.avi", size=(WIDTH, HEIGHT))
    settings = make_settings(tmp_path, default_video_id="demo")
    ConfigStore(settings.configs_dir).save(TableConfig(
        video_id="demo",
        source=str(video),
        frame_width=WIDTH,
        frame_height=HEIGHT,
        tables=[TABLE_1],
        occupancy=OccupancySettings(confidence_threshold=0.1, enter_seconds=0, leave_seconds=10),
    ))
    app = create_app(settings, detector_factory=lambda _settings: FakeDetector(), popen=FakePopen())
    with TestClient(app) as test_client:
        wait_until(lambda: test_client.get("/api/health").json()["source"] == "LIVE")
        yield test_client


def table_status(client: TestClient) -> dict[str, str]:
    return {table["id"]: table["status"] for table in client.get("/api/tables").json()}


def test_health(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["pipeline_running"] and health["model"] == "ready"
    assert health["video_id"] == "demo" and health["device"] == "cpu"
    assert health["mediamtx"] == "not running" and health["mediamtx_managed"] is False
    assert health["ffmpeg"] == "stopped"  # "demo" is a camera config, not an uploaded video


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


def test_runs_are_recorded_and_analytics_count_the_occupied_time(client: TestClient) -> None:
    wait_until(lambda: table_status(client).get("T1") == "OCCUPIED")
    time.sleep(1.0)
    watched = client.get("/api/analytics/videos").json()
    assert [(v["id"], v["name"], v["is_live"]) for v in watched] == [("demo", "demo", True)]

    result = client.get("/api/analytics").json()  # the live video by default
    assert result["video_id"] == "demo" and result["table_count"] == 1
    table = result["tables"][0]
    assert table["name"] == "Table 1" and table["session_count"] == 1
    assert 0.5 < table["occupied_seconds"] <= result["monitored_seconds"]  # the person never leaves
    assert result["average_occupancy"] > 0.5
    assert result["timeline"] and result["timeline"][-1]["occupied_tables"] > 0

    since = result["until"] + 5
    assert client.get("/api/analytics", params={"since": since}).status_code == 422
    empty = client.get("/api/analytics", params={"video_id": "nothing"}).json()
    assert empty["video_id"] == "nothing" and empty["tables"] == [] and empty["timeline"] == []


def test_a_new_run_starts_when_the_video_is_restarted(client: TestClient) -> None:
    pipeline = client.app.state.pipeline
    db = client.app.state.db
    first_run = pipeline._run
    pipeline.activate("demo")  # watching again: occupancy starts over
    assert pipeline._run != first_run
    pipeline.activate(None)  # idle: no run
    assert pipeline._run is None
    runs = db.runs_between("demo", 0, time.time(), time.time())
    assert len(runs) == 2 and all(end >= start for start, end in runs)
