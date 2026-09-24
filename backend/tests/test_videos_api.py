"""Tests for video upload, listing, deletion and fake-camera control through the API."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config_store import ConfigStore
from app.main import create_app
from app.schemas import OccupancySettings, TableConfig, TableDef
from conftest import FakeDetector, FakePopen, TcpListener, make_settings, write_video


class App:
    def __init__(self, client: TestClient, popen: FakePopen, tmp_path: Path) -> None:
        self.client, self.popen, self.tmp_path = client, popen, tmp_path

    def upload(self, path: Path, name: str | None = None) -> dict:
        with path.open("rb") as file:
            response = self.client.post("/api/videos", files={"file": (name or path.name, file, "video/mp4")})
        assert response.status_code == 201, response.text
        return response.json()


@pytest.fixture
def app(tmp_path: Path) -> Iterator[App]:
    rtsp_server = TcpListener()  # stands in for a running MediaMTX
    rtsp_url = f"rtsp://127.0.0.1:{rtsp_server.port}/cam1"
    settings = make_settings(tmp_path, max_upload_mb=1, fake_camera_rtsp_url=rtsp_url)
    # A video copied into the videos folder by hand before startup.
    write_video(settings.videos_dir / "lobby.avi", frames=10)
    popen = FakePopen()
    try:
        with TestClient(create_app(settings, lambda _s: FakeDetector(), popen=popen)) as client:
            yield App(client, popen, tmp_path)
    finally:
        rtsp_server.close()


def test_upload_reads_metadata_and_makes_a_thumbnail(app: App) -> None:
    video = app.upload(write_video(app.tmp_path / "src" / "clip.mp4", frames=40, fps=20), name="My clip.mp4")
    assert video["name"] == "My clip.mp4" and video["filename"] == f"{video['id']}.mp4"
    assert (video["width"], video["height"]) == (320, 180)
    assert video["fps"] == pytest.approx(20, abs=0.1)
    assert video["duration_seconds"] == pytest.approx(2.0, abs=0.2)
    assert video["has_tables"] is False and video["is_streaming"] is False
    assert (app.tmp_path / "videos" / video["filename"]).is_file()
    assert not list((app.tmp_path / "videos").glob("*.part"))

    thumbnail = app.client.get(video["thumbnail_url"])
    assert thumbnail.status_code == 200 and thumbnail.content[:2] == b"\xff\xd8"
    ids = [v["id"] for v in app.client.get("/api/videos").json()]
    assert ids[0] == video["id"]  # newest first


def test_dashboard_origin_is_allowed_by_cors(app: App) -> None:
    allowed = app.client.get("/api/videos", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    other = app.client.get("/api/videos", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_upload_limits(app: App) -> None:
    limits = app.client.get("/api/videos/limits").json()
    assert limits == {"max_upload_mb": 1, "extensions": [".mp4", ".avi", ".mov", ".mkv"]}


def test_videos_already_in_the_folder_are_listed_by_file_name(app: App) -> None:
    videos = {v["id"]: v for v in app.client.get("/api/videos").json()}
    assert "lobby" in videos and videos["lobby"]["name"] == "lobby.avi"


@pytest.mark.parametrize(
    ("name", "content", "status"),
    [
        ("notes.txt", b"hello", 415),
        ("fake.mp4", b"this is not a video at all" * 100, 422),
        ("empty.mp4", b"", 400),
        ("huge.mp4", b"\0" * (1024 * 1024 + 100 * 1024), 413),  # over MAX_UPLOAD_MB=1
    ],
    ids=["wrong-type", "not-a-video", "empty", "too-large"],
)
def test_invalid_uploads_are_rejected_and_nothing_is_kept(app: App, name: str, content: bytes, status: int) -> None:
    response = app.client.post("/api/videos", files={"file": (name, content, "application/octet-stream")})
    assert response.status_code == status, response.text
    assert sorted(p.name for p in (app.tmp_path / "videos").iterdir()) == ["lobby.avi"]
    assert [v["id"] for v in app.client.get("/api/videos").json()] == ["lobby"]


def test_start_switch_and_stop_the_fake_camera(app: App) -> None:
    first = app.upload(write_video(app.tmp_path / "src" / "a.mp4"))
    ConfigStore(app.tmp_path / "configs").save(TableConfig(
        video_id=first["id"], source="rtsp://127.0.0.1:9/cam1", frame_width=320, frame_height=180,
        tables=[TableDef(id="T1", name="Table 1", polygon=[(20, 20), (150, 20), (150, 170), (20, 170)])],
        occupancy=OccupancySettings(confidence_threshold=0.1, enter_seconds=0, leave_seconds=10),
    ))

    status = app.client.post("/api/stream/start", json={"video_id": first["id"]}).json()
    assert status["state"] == "running" and status["video_id"] == first["id"]
    assert status["video_name"] == "a.mp4" and status["has_tables"] is True
    assert app.client.get("/api/health").json()["video_id"] == first["id"]  # pipeline switched too
    listed = {v["id"]: v for v in app.client.get("/api/videos").json()}
    assert listed[first["id"]]["is_streaming"] and listed[first["id"]]["table_count"] == 1

    status = app.client.post("/api/stream/start", json={"video_id": "lobby"}).json()
    assert status["video_id"] == "lobby" and status["has_tables"] is False
    old_ffmpeg, new_ffmpeg = app.popen.ffmpeg()
    assert old_ffmpeg.terminated and new_ffmpeg.poll() is None
    assert new_ffmpeg.args[new_ffmpeg.args.index("-i") + 1].endswith("lobby.avi")

    status = app.client.post("/api/stream/stop").json()
    assert status["state"] == "stopped" and new_ffmpeg.terminated
    assert app.client.get("/api/stream/status").json()["state"] == "stopped"
    health = app.client.get("/api/health").json()
    assert health["video_id"] is None and health["source"] == "NO SOURCE"  # idle, not retrying


def test_idle_until_a_stream_is_started(app: App) -> None:
    health = app.client.get("/api/health").json()
    assert health["video_id"] is None and health["source"] == "NO SOURCE"
    assert app.client.get("/api/snapshot").status_code == 503


def test_starting_an_unknown_video_is_a_404_listing_the_ids(app: App) -> None:
    response = app.client.post("/api/stream/start", json={"video_id": "string"})
    assert response.status_code == 404
    assert "'string'" in response.json()["detail"] and "lobby" in response.json()["detail"]
    assert app.client.post("/api/stream/start", json={"video_id": "../x"}).status_code == 422


def test_ffmpeg_crash_shows_as_error(app: App) -> None:
    app.client.post("/api/stream/start", json={"video_id": "lobby"})
    app.popen.ffmpeg()[0].crash(code=1, message="Broken pipe")
    deadline = time.monotonic() + 5
    while app.client.get("/api/stream/status").json()["state"] != "error" and time.monotonic() < deadline:
        time.sleep(0.05)
    status = app.client.get("/api/stream/status").json()
    assert status["state"] == "error" and "Broken pipe" in status["error"]
    assert app.client.get("/api/health").json()["ffmpeg"] == "error"


def test_deleting_the_streaming_video_stops_it_and_removes_everything(app: App) -> None:
    video = app.upload(write_video(app.tmp_path / "src" / "b.mp4"))
    ConfigStore(app.tmp_path / "configs").save(TableConfig(
        video_id=video["id"], source="rtsp://127.0.0.1:9/cam1", frame_width=320, frame_height=180,
        tables=[TableDef(id="T1", name="Table 1", polygon=[(20, 20), (150, 20), (150, 170), (20, 170)])],
        occupancy=OccupancySettings(confidence_threshold=0.1, enter_seconds=0, leave_seconds=10),
    ))
    app.client.post("/api/stream/start", json={"video_id": video["id"]})
    assert [v["id"] for v in app.client.get("/api/analytics/videos").json()] == [video["id"]]

    assert app.client.delete(f"/api/videos/{video['id']}").status_code == 204
    assert app.client.get("/api/analytics/videos").json() == []  # its history is gone too
    assert app.popen.ffmpeg()[-1].terminated
    assert app.client.get("/api/stream/status").json()["state"] == "stopped"
    assert app.client.get("/api/health").json()["video_id"] is None
    assert not (app.tmp_path / "videos" / video["filename"]).exists()
    assert not (app.tmp_path / "configs" / f"{video['id']}.json").exists()
    assert app.client.get(video["thumbnail_url"]).status_code == 404
    assert app.client.delete(f"/api/videos/{video['id']}").status_code == 404
