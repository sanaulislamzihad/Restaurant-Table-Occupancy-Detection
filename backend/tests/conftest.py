"""Shared test helpers: a fake person detector, fake child processes and test settings."""

from __future__ import annotations

import queue
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import supervision as sv

from app.settings import BACKEND_DIR, Settings


class FakeDetector:
    """Always sees one person whose feet are at (80, 150)."""

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


class FakeProcess:
    """Stands in for a subprocess.Popen child: "runs" until terminated or crash()ed."""

    _next_pid = 1000

    def __init__(self, args: list[str], **kwargs: Any) -> None:
        FakeProcess._next_pid += 1
        self.pid = FakeProcess._next_pid
        self.args = list(args)
        self.kwargs = kwargs
        self.returncode: int | None = None
        self.terminated = False
        self.stdout = self  # the manager reads output with stdout.readline()
        self._lines: queue.Queue[str] = queue.Queue()
        self._exited = threading.Event()

    def readline(self) -> str:
        while True:
            try:
                return self._lines.get(timeout=0.02)
            except queue.Empty:
                if self._exited.is_set():
                    return ""

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self._exited.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        return self.returncode  # type: ignore[return-value]

    def terminate(self) -> None:
        self.terminated = True
        self._exit(1)

    def kill(self) -> None:
        self._exit(-9)

    def crash(self, code: int = 1, message: str | None = None) -> None:
        if message:
            self._lines.put(message + "\n")
        self._exit(code)

    def _exit(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code
            self._exited.set()


class FakePopen:
    """Records every process the code under test starts."""

    def __init__(self) -> None:
        self.processes: list[FakeProcess] = []

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(args, **kwargs)
        self.processes.append(process)
        return process

    def ffmpeg(self) -> list[FakeProcess]:
        return [p for p in self.processes if "-stream_loop" in p.args]

    def mediamtx(self) -> list[FakeProcess]:
        return [p for p in self.processes if "-stream_loop" not in p.args]


class TcpListener:
    """A port that accepts connections and closes them at once: "an RTSP server is running"."""

    def __init__(self) -> None:
        self._socket = socket.create_server(("127.0.0.1", 0))
        self.port = self._socket.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._closed:
            try:
                connection, _ = self._socket.accept()
                connection.close()
            except OSError:
                return

    def close(self) -> None:
        self._closed = True
        self._socket.close()


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings from .env.example with all folders under tmp_path and no real processes."""
    values: dict[str, Any] = {
        "configs_dir": tmp_path / "configs",
        "data_dir": tmp_path / "data",
        "videos_dir": tmp_path / "videos",
        "default_video_id": "",
        "fake_camera_rtsp_url": "rtsp://127.0.0.1:9/cam1",  # nothing listens on port 9
        "manage_mediamtx": False,
        # Any existing executable: FakePopen never runs it.
        "ffmpeg_path": sys.executable,
        "mediamtx_path": sys.executable,
        "loop_video_files": True,
        "detect_every_n_frames": 1,
        # Short timeouts so a missing camera never slows a test down.
        "source_reconnect_seconds": 0.5,
        "source_open_timeout_seconds": 2,
        "source_read_timeout_seconds": 2,
    }
    values.update(overrides)
    return Settings(_env_file=BACKEND_DIR / ".env.example").model_copy(update=values)  # type: ignore[call-arg]


def write_video(path: Path, frames: int = 20, size: tuple[int, int] = (320, 180), fps: int = 20) -> Path:
    """A small real video file (MJPG in .avi, or mp4v in .mp4)."""
    codec = "mp4v" if path.suffix == ".mp4" else "MJPG"
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), fps, size)
    for i in range(frames):
        writer.write(np.full((size[1], size[0], 3), 40 + i * 5, np.uint8))
    writer.release()
    return path
