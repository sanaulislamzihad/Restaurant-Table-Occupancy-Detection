"""Tests for the table editor API: editor frames with hints, and saving tables of any video."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from conftest import FakeDetector, FakePopen, TcpListener, make_settings, write_video

TABLE = [(20, 20), (150, 20), (150, 170), (20, 170)]


class FakeSceneHints:
    """Always finds one person and one table; counts how often it was asked."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def analyze(self, frame: np.ndarray, *, person_confidence: float, reference_point: str):
        self.calls.append({"shape": frame.shape, "confidence": person_confidence, "point": reference_point})
        return [(80, 150)], [[(10, 10), (160, 10), (160, 175), (10, 175)]]


class Editor:
    def __init__(self, client: TestClient, hints: FakeSceneHints, factory_calls: list[Settings], tmp: Path) -> None:
        self.client, self.hints, self.factory_calls, self.tmp = client, hints, factory_calls, tmp

    @property
    def pipeline(self):
        return self.client.app.state.pipeline

    def put_tables(self, video_id: str, tables: list[dict], **size: int):
        return self.client.put(f"/api/videos/{video_id}/tables", json={"tables": tables, **size})


def make_editor(tmp_path: Path, hints_factory) -> Iterator[Editor]:
    rtsp_server = TcpListener()
    settings = make_settings(tmp_path, fake_camera_rtsp_url=f"rtsp://127.0.0.1:{rtsp_server.port}/cam1")
    write_video(settings.videos_dir / "lobby.avi", frames=10)
    write_video(settings.videos_dir / "hall.avi", frames=10, size=(640, 360))
    hints = FakeSceneHints()
    factory_calls: list[Settings] = []

    def factory(s: Settings):
        factory_calls.append(s)
        return hints_factory(hints)

    try:
        app = create_app(settings, lambda _s: FakeDetector(), popen=FakePopen(), hints_factory=factory)
        with TestClient(app) as client:
            yield Editor(client, hints, factory_calls, tmp_path)
    finally:
        rtsp_server.close()


@pytest.fixture
def editor(tmp_path: Path) -> Iterator[Editor]:
    yield from make_editor(tmp_path, lambda hints: hints)


def decode(data_url: str) -> np.ndarray:
    prefix = "data:image/jpeg;base64,"
    assert data_url.startswith(prefix)
    image = cv2.imdecode(np.frombuffer(base64.b64decode(data_url[len(prefix):]), np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    return image


def test_editor_frame_from_the_file_with_people_and_suggestions(editor: Editor) -> None:
    frame = editor.client.get("/api/videos/lobby/editor-frame").json()
    assert (frame["frame_width"], frame["frame_height"]) == (320, 180)
    assert decode(frame["image"]).shape == (180, 320, 3)
    assert frame["live"] is False and frame["at_seconds"] == 1.0  # 0.5 s video: first frame is used
    assert frame["people"] == [[80, 150]]
    assert frame["suggested_tables"] == [[[10, 10], [160, 10], [160, 175], [10, 175]]]
    assert frame["hints_error"] is None

    editor.client.get("/api/videos/hall/editor-frame", params={"at": 0.2})
    assert len(editor.factory_calls) == 1  # the model is loaded once
    assert editor.hints.calls[-1]["shape"] == (360, 640, 3)
    assert editor.hints.calls[-1]["point"] == "bottom_center"


def test_editor_frame_without_hints_skips_the_model(editor: Editor) -> None:
    frame = editor.client.get("/api/videos/lobby/editor-frame", params={"hints": False}).json()
    assert frame["people"] == [] and frame["suggested_tables"] == []
    assert editor.factory_calls == [] and editor.hints.calls == []


def test_editor_frame_uses_the_live_frame_of_the_streaming_video(editor: Editor, monkeypatch) -> None:
    editor.client.post("/api/stream/start", json={"video_id": "lobby"})
    live_frame = np.zeros((200, 400, 3), np.uint8)
    monkeypatch.setattr(editor.pipeline, "latest_frame", lambda: live_frame.copy())

    frame = editor.client.get("/api/videos/lobby/editor-frame").json()
    assert frame["live"] is True and frame["at_seconds"] is None
    assert (frame["frame_width"], frame["frame_height"]) == (400, 200)
    other = editor.client.get("/api/videos/hall/editor-frame").json()
    assert other["live"] is False and other["frame_width"] == 640  # not the streaming video
    from_file = editor.client.get("/api/videos/lobby/editor-frame", params={"live": False}).json()
    assert from_file["live"] is False and from_file["frame_width"] == 320


def test_editor_frame_still_works_when_the_model_fails(tmp_path: Path) -> None:
    def broken(_hints):
        raise RuntimeError("weights missing")

    for editor in make_editor(tmp_path, broken):
        frame = editor.client.get("/api/videos/lobby/editor-frame")
        assert frame.status_code == 200
        body = frame.json()
        assert body["people"] == [] and "weights missing" in body["hints_error"]
        decode(body["image"])


def test_unknown_video_is_a_404(editor: Editor) -> None:
    assert editor.client.get("/api/videos/nope/editor-frame").status_code == 404
    assert editor.client.get("/api/videos/nope/config").status_code == 404
    assert editor.put_tables("nope", [], frame_width=320, frame_height=180).status_code == 404


def test_tables_of_a_video_that_is_not_streaming_are_saved(editor: Editor) -> None:
    assert editor.client.get("/api/videos/hall/config").status_code == 404
    table = {"id": "T1", "name": "Window", "polygon": TABLE}
    response = editor.put_tables("hall", [table], frame_width=320, frame_height=180)
    assert response.status_code == 200, response.text
    saved = editor.client.get("/api/videos/hall/config").json()
    assert saved["tables"][0]["name"] == "Window" and (saved["frame_width"], saved["frame_height"]) == (320, 180)
    assert saved["source"] == editor.client.app.state.streams.status().rtsp_url
    listed = {v["id"]: v for v in editor.client.get("/api/videos").json()}
    assert listed["hall"]["table_count"] == 1 and listed["hall"]["has_tables"] is True

    # Occupancy settings edited in the file are kept when the tables change.
    path = editor.tmp / "configs" / "hall.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["occupancy"]["enter_seconds"] = 2.5
    path.write_text(json.dumps(data), encoding="utf-8")
    renamed = {**table, "name": "Corner"}
    saved = editor.put_tables("hall", [renamed]).json()  # size taken from the existing config
    assert saved["occupancy"]["enter_seconds"] == 2.5 and saved["tables"][0]["name"] == "Corner"
    assert saved["frame_width"] == 320


def test_unusable_tables_are_rejected_and_nothing_is_saved(editor: Editor) -> None:
    sliver = {"id": "T1", "name": "Sliver", "polygon": [(10, 10), (300, 10), (300, 14), (10, 14)]}
    response = editor.put_tables("hall", [sliver], frame_width=640, frame_height=360)
    assert response.status_code == 422 and "Sliver" in response.json()["detail"]
    no_size = editor.put_tables("hall", [{"id": "T1", "name": "A", "polygon": TABLE}])
    assert no_size.status_code == 422 and "frame_width" in no_size.json()["detail"]
    duplicate = [{"id": "T1", "name": "A", "polygon": TABLE}, {"id": "T1", "name": "B", "polygon": TABLE}]
    assert editor.put_tables("hall", duplicate, frame_width=640, frame_height=360).status_code == 422
    assert not (editor.tmp / "configs" / "hall.json").exists()


def test_tables_of_the_streaming_video_are_used_at_once(editor: Editor) -> None:
    editor.client.post("/api/stream/start", json={"video_id": "lobby"})
    assert editor.client.get("/api/config").status_code == 404
    tables = [{"id": "T1", "name": "Table 1", "polygon": TABLE}]
    assert editor.put_tables("lobby", tables, frame_width=320, frame_height=180).status_code == 200
    live = editor.client.get("/api/config").json()  # what the pipeline uses now
    assert live["video_id"] == "lobby" and [t["name"] for t in live["tables"]] == ["Table 1"]
    assert editor.client.get("/api/stream/status").json()["has_tables"] is True
