"""Tests for the fake-camera process manager, with fake child processes."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.stream_manager import StreamManager, StreamState
from conftest import FakePopen, make_settings


def wait_until(condition, timeout: float = 5.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def popen() -> FakePopen:
    return FakePopen()


def manager(tmp_path: Path, popen: FakePopen, server_up: bool = True, **settings: object) -> StreamManager:
    """A manager whose RTSP port check answers from server_up or a running fake MediaMTX."""

    def reachable(_url: str, _timeout: float) -> bool:
        return server_up or any(p.poll() is None for p in popen.mediamtx())

    return StreamManager(make_settings(tmp_path, **settings), popen=popen, reachable=reachable)


def test_start_runs_ffmpeg_with_the_looping_command(tmp_path: Path, popen: FakePopen) -> None:
    streams = manager(tmp_path, popen)
    status = streams.start("abc", tmp_path / "abc.mp4")
    assert status.state == "running" and status.video_id == "abc" and status.uptime_seconds is not None
    args = popen.ffmpeg()[0].args
    assert args[args.index("-i") + 1] == str(tmp_path / "abc.mp4")
    for flag in ("-re", "-nostdin"):
        assert flag in args
    assert args[args.index("-stream_loop") + 1] == "-1"
    assert args[-1] == "rtsp://127.0.0.1:9/cam1"
    assert args[args.index("-c:v") + 1] == "libx264"


def test_switching_videos_stops_the_previous_ffmpeg(tmp_path: Path, popen: FakePopen) -> None:
    streams = manager(tmp_path, popen)
    streams.start("first", tmp_path / "first.mp4")
    streams.start("second", tmp_path / "second.mp4")
    first, second = popen.ffmpeg()
    assert first.terminated and second.poll() is None
    assert streams.status().video_id == "second" and streams.state is StreamState.RUNNING


def test_stop(tmp_path: Path, popen: FakePopen) -> None:
    streams = manager(tmp_path, popen)
    streams.start("abc", tmp_path / "abc.mp4")
    status = streams.stop()
    assert popen.ffmpeg()[0].terminated
    assert status.state == "stopped" and status.video_id is None and status.error is None


def test_ffmpeg_dying_on_its_own_is_reported_as_an_error(tmp_path: Path, popen: FakePopen) -> None:
    streams = manager(tmp_path, popen)
    streams.start("abc", tmp_path / "abc.mp4")
    popen.ffmpeg()[0].crash(code=1, message="Connection refused")
    assert wait_until(lambda: streams.state is StreamState.ERROR)
    status = streams.status()
    assert status.video_id == "abc"
    assert "code 1" in status.error and "Connection refused" in status.error


def test_a_planned_stop_is_not_an_error(tmp_path: Path, popen: FakePopen) -> None:
    streams = manager(tmp_path, popen)
    streams.start("abc", tmp_path / "abc.mp4")
    streams.stop()
    time.sleep(0.2)  # give the watcher thread time to see the exit
    assert streams.state is StreamState.STOPPED


def test_mediamtx_is_started_when_nothing_listens(tmp_path: Path, popen: FakePopen) -> None:
    streams = manager(tmp_path, popen, server_up=False, manage_mediamtx=True)
    assert streams.ensure_mediamtx()
    mediamtx = popen.mediamtx()[0]
    assert mediamtx.kwargs["env"]["MTX_RTSPADDRESS"] == ":9"  # port from FAKE_CAMERA_RTSP_URL
    assert str(mediamtx.args[1]).endswith("mediamtx.yml")
    status = streams.status()
    assert status.mediamtx == "running" and status.mediamtx_managed

    streams.shutdown()
    assert mediamtx.terminated


def test_running_server_is_reused_and_left_alone(tmp_path: Path, popen: FakePopen) -> None:
    streams = manager(tmp_path, popen, server_up=True, manage_mediamtx=True)
    assert streams.ensure_mediamtx()
    assert popen.mediamtx() == []
    assert streams.status().mediamtx_managed is False


def test_no_server_and_not_managed_fails_the_stream(tmp_path: Path, popen: FakePopen) -> None:
    streams = manager(tmp_path, popen, server_up=False, manage_mediamtx=False)
    status = streams.start("abc", tmp_path / "abc.mp4")
    assert status.state == "error" and "MediaMTX" in status.error
    assert popen.ffmpeg() == []


def test_missing_ffmpeg_is_reported(tmp_path: Path, popen: FakePopen) -> None:
    streams = manager(tmp_path, popen, ffmpeg_path=str(tmp_path / "no-ffmpeg-here"))
    status = streams.start("abc", tmp_path / "abc.mp4")
    assert status.state == "error" and "ffmpeg not found" in status.error


def test_shutdown_stops_everything(tmp_path: Path, popen: FakePopen) -> None:
    streams = manager(tmp_path, popen, server_up=False, manage_mediamtx=True)
    streams.start("abc", tmp_path / "abc.mp4")
    streams.shutdown()
    assert all(process.terminated for process in popen.processes) and len(popen.processes) == 2
