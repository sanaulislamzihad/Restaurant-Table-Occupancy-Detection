"""Tests for FrameSource using a small generated video file and an unreachable stream."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pytest
from loguru import logger

from app.sources import FrameSource, SourceStatus, redact, stream_problem

FPS = 20
FRAME_COUNT = 10
WIDTH, HEIGHT = 64, 48


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A short video whose frame i is a flat grey image with brightness 20 * i."""
    path = tmp_path_factory.mktemp("video") / "sample.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (WIDTH, HEIGHT))
    for i in range(FRAME_COUNT):
        writer.write(np.full((HEIGHT, WIDTH, 3), 20 * i, np.uint8))
    writer.release()
    return path


def frame_index(frame: np.ndarray) -> int:
    """Recover the index of a sample_video frame from its brightness."""
    return round(float(frame.mean()) / 20)


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_redact_hides_url_password() -> None:
    assert redact("rtsp://admin:secret@192.168.1.10:554/stream") == "rtsp://admin:***@192.168.1.10:554/stream"
    assert redact("rtsp://localhost:8554/cam1") == "rtsp://localhost:8554/cam1"


def test_file_reports_frames_size_and_fps(sample_video: Path) -> None:
    with FrameSource(str(sample_video), loop_files=False) as source:
        ok, frame = source.read(timeout=5)
        assert ok and frame is not None
        assert frame.shape == (HEIGHT, WIDTH, 3)
        assert source.resolution == (WIDTH, HEIGHT)
        assert source.fps == pytest.approx(FPS)
        assert source.status is SourceStatus.LIVE
        assert source.is_alive()


def test_file_plays_every_frame_in_real_time_then_ends(sample_video: Path) -> None:
    indexes = []
    start = time.monotonic()
    with FrameSource(str(sample_video), loop_files=False) as source:
        while True:
            ok, frame = source.read(timeout=2)
            if not ok:
                break
            indexes.append(frame_index(frame))
        elapsed = time.monotonic() - start
        assert source.status is SourceStatus.ENDED
        assert not source.is_alive()  # right away, even while the file is still being closed
    assert indexes == list(range(FRAME_COUNT))  # each frame once, in order
    assert elapsed >= 0.8 * (FRAME_COUNT - 1) / FPS  # paced at the file's frame rate


def test_slow_reader_skips_to_the_latest_frame(sample_video: Path) -> None:
    with FrameSource(str(sample_video), loop_files=False) as source:
        ok, first = source.read(timeout=5)
        assert ok and first is not None
        time.sleep(5 / FPS)  # the consumer is busy for about five frames
        ok, latest = source.read(timeout=1)
        assert ok and latest is not None
        assert frame_index(latest) - frame_index(first) >= 3  # stale frames were dropped


def test_file_loops_when_enabled(sample_video: Path) -> None:
    with FrameSource(str(sample_video), loop_files=True) as source:
        frames = 0
        deadline = time.monotonic() + 2.5 * FRAME_COUNT / FPS
        while time.monotonic() < deadline:
            ok, _ = source.read(timeout=1)
            frames += ok
        assert frames > FRAME_COUNT  # went past the end and started again
        assert source.is_alive()


def test_missing_file_ends_without_raising(tmp_path: Path) -> None:
    with FrameSource(str(tmp_path / "missing.mp4")) as source:
        assert wait_until(lambda: source.status is SourceStatus.ENDED)
        assert source.read(timeout=0.1) == (False, None)
        assert not source.is_alive()


def test_unreachable_stream_keeps_retrying_and_releases_quickly() -> None:
    source = FrameSource(
        "rtsp://127.0.0.1:9/cam",  # nothing listens on port 9
        reconnect_seconds=0.2,
        open_timeout_seconds=1,
        read_timeout_seconds=1,
    )
    try:
        assert wait_until(lambda: source.status is SourceStatus.RECONNECTING, timeout=10)
        assert source.is_alive()
        assert source.read(timeout=0.1) == (False, None)
    finally:
        start = time.monotonic()
        source.release()
        assert time.monotonic() - start < 5
    assert source.status is SourceStatus.STOPPED
    assert not source.is_alive()


def test_retry_warnings_are_not_repeated_every_attempt() -> None:
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(message), level="WARNING", format="{message}")
    source = FrameSource("rtsp://127.0.0.1:9/cam", reconnect_seconds=0.05, open_timeout_seconds=0.2)
    try:
        assert wait_until(lambda: source._failures >= 5, timeout=15)
    finally:
        source.release()
        logger.remove(sink)
    retry_warnings = [w for w in warnings if "Could not open" in w]
    assert len(retry_warnings) == 1  # once, not every 0.05 s
    assert "nothing answers at that address" in retry_warnings[0]


class FakeRtspServer:
    """Answers every request with one fixed RTSP status line."""

    def __init__(self, status: str) -> None:
        self._socket = socket.create_server(("127.0.0.1", 0))
        self.url = f"rtsp://user:secret@127.0.0.1:{self._socket.getsockname()[1]}/cam1"
        self.requests: list[bytes] = []
        self._status = status
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            with connection:
                request = connection.recv(1024)
                self.requests.append(request)
                if request:  # a real request, not just a TCP check
                    connection.sendall(f"RTSP/1.0 {self._status}\r\nCSeq: 1\r\n\r\n".encode())

    def close(self) -> None:
        self._socket.close()


def test_stream_problem_explains_why_there_is_no_video() -> None:
    assert "nothing answers" in stream_problem("rtsp://127.0.0.1:9/cam", timeout=1)

    missing = FakeRtspServer("404 Not Found")
    login = FakeRtspServer("401 Unauthorized")
    try:
        assert "no stream at this path" in stream_problem(missing.url, timeout=1)
        assert stream_problem(login.url, timeout=1) is None  # left to OpenCV, which can log in
        describe = next(r for r in missing.requests if r)
        assert describe.startswith(b"DESCRIBE rtsp://127.0.0.1:") and b"secret" not in describe
    finally:
        missing.close()
        login.close()


def test_release_twice_is_safe(sample_video: Path) -> None:
    source = FrameSource(str(sample_video))
    source.release()
    source.release()
    assert source.status is SourceStatus.STOPPED
    assert source.read(timeout=0.1) == (False, None)
