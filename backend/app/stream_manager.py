"""The fake CCTV camera, run by the backend: MediaMTX (RTSP server) and ffmpeg.

* ``ensure_mediamtx()`` makes sure an RTSP server listens on the port of
  FAKE_CAMERA_RTSP_URL, starting MediaMTX from MEDIAMTX_PATH when nothing does
  and MANAGE_MEDIAMTX is on.
* ``start()`` stops the running ffmpeg (if any) and starts a new one that
  loops a video in real time to FAKE_CAMERA_RTSP_URL.
* ``stop()``, ``status()`` and ``shutdown()``.

The output of both programs goes to the log. If ffmpeg exits without being
asked to, the state becomes "error" with its last messages. Child processes
are tied to the backend, so none are left running after it stops or crashes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from loguru import logger

from app.process_guard import ChildProcessGuard
from app.schemas import StreamStatusOut
from app.settings import BACKEND_DIR, Settings
from app.sources import is_reachable

MEDIAMTX_START_TIMEOUT_S = 10.0
STOP_TIMEOUT_S = 5.0
# ffmpeg gives up if the RTSP server does not answer for this long (without it a
# failed connection can hang forever on Windows).
FFMPEG_SOCKET_TIMEOUT_US = 5_000_000
LOG_TAIL_LINES = 20


class StreamState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


def resolve_executable(value: str) -> str | None:
    """A command on PATH, or a path relative to backend/ (".exe" optional on Windows)."""
    if "/" not in value and "\\" not in value:
        return shutil.which(value)
    path = (BACKEND_DIR / value).resolve()
    for candidate in (path, path.with_name(path.name + ".exe")):
        if candidate.is_file():
            return str(candidate)
    return None


def ffmpeg_command(ffmpeg: str, video: Path, rtsp_url: str) -> list[str]:
    """Arguments that publish a video in real time, looping forever.

    The same command as fake_camera/start_camera.py, without the interactive parts.
    """
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-re",                                          # real-time speed, like a live camera
        "-stream_loop", "-1",                           # loop the file forever
        "-i", str(video),
        "-map", "0:v:0",                                # first video stream only, no audio
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",     # H.264 needs an even width and height
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:gte(t,n_forced*2)",  # keyframe every 2 s so viewers start fast
        "-f", "rtsp", "-rtsp_transport", "tcp", "-timeout", str(FFMPEG_SOCKET_TIMEOUT_US),
        rtsp_url,
    ]


def _terminate(process: subprocess.Popen[Any]) -> None:
    """Stop a child process, killing it if it does not exit in time."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


class StreamManager:
    """Starts, stops and watches MediaMTX and ffmpeg."""

    def __init__(
        self,
        settings: Settings,
        popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        reachable: Callable[[str, float], bool] = is_reachable,
    ) -> None:
        self.settings = settings
        self.rtsp_url = settings.fake_camera_rtsp_url
        self._port = urlsplit(self.rtsp_url).port or 554
        self._popen = popen
        self._reachable = reachable
        self._guard = ChildProcessGuard()

        self._operation = threading.Lock()  # one start/stop/shutdown at a time
        self._lock = threading.RLock()  # guards the state below
        self._mediamtx: subprocess.Popen[Any] | None = None
        self._ffmpeg: subprocess.Popen[Any] | None = None
        self._state = StreamState.STOPPED
        self._video_id: str | None = None
        self._started_at: float | None = None
        self._error: str | None = None
        self._ffmpeg_tail: deque[str] = deque(maxlen=LOG_TAIL_LINES)

    # ------------------------------------------------------------------ public API

    @property
    def video_id(self) -> str | None:
        """Video being streamed (or that failed)."""
        return self._video_id

    @property
    def state(self) -> StreamState:
        return self._state

    def ensure_mediamtx(self) -> bool:
        """Make sure the RTSP server is up, starting MediaMTX if needed. True if it is up."""
        with self._lock:
            if self._mediamtx is not None and self._mediamtx.poll() is None:
                return True
        if self._reachable(self.rtsp_url, 0.5):
            logger.info("Using the RTSP server that is already running on port {}", self._port)
            return True
        if not self.settings.manage_mediamtx:
            logger.warning("No RTSP server on port {} and MANAGE_MEDIAMTX is off", self._port)
            return False
        executable = resolve_executable(self.settings.mediamtx_path)
        if executable is None:
            logger.error("MediaMTX not found at {}; run fake_camera/download_mediamtx.py",
                         self.settings.mediamtx_path)
            return False
        config = self.settings.mediamtx_config
        # MTX_RTSPADDRESS overrides the port in mediamtx.yml with the one from backend/.env.
        env = {**os.environ, "MTX_RTSPADDRESS": f":{self._port}"}
        process = self._spawn([executable, str(config)], "mediamtx", cwd=config.parent, env=env)
        with self._lock:
            self._mediamtx = process

        deadline = time.monotonic() + MEDIAMTX_START_TIMEOUT_S
        while time.monotonic() < deadline:
            if process.poll() is not None:
                logger.error("MediaMTX exited with code {} right after starting", process.returncode)
                return False
            if self._reachable(self.rtsp_url, 0.5):
                logger.info("MediaMTX started on port {} (pid {})", self._port, process.pid)
                return True
            time.sleep(0.2)
        logger.error("MediaMTX did not open port {} within {:.0f} s", self._port, MEDIAMTX_START_TIMEOUT_S)
        return False

    def start(self, video_id: str, video_path: Path) -> StreamStatusOut:
        """Stream a video as the fake camera, replacing whatever was streaming."""
        with self._operation:
            with self._lock:
                self._stop_ffmpeg()
                self._video_id, self._started_at = video_id, None
            ffmpeg = resolve_executable(self.settings.ffmpeg_path)
            if ffmpeg is None:
                self._fail(f"ffmpeg not found ('{self.settings.ffmpeg_path}'): install it or set FFMPEG_PATH")
            elif not self.ensure_mediamtx():
                self._fail("The RTSP server (MediaMTX) is not running")
            else:
                with self._lock:
                    self._ffmpeg_tail.clear()
                    process = self._spawn(ffmpeg_command(ffmpeg, video_path, self.rtsp_url), "ffmpeg")
                    self._ffmpeg = process
                    self._state, self._started_at, self._error = StreamState.RUNNING, time.time(), None
                logger.info("Streaming {} to {} (ffmpeg pid {})", video_path.name, self.rtsp_url, process.pid)
        return self.status()

    def stop(self) -> StreamStatusOut:
        """Stop streaming (MediaMTX keeps running)."""
        with self._operation, self._lock:
            if self._ffmpeg is not None:
                logger.info("Stopping the stream of {}", self._video_id)
            self._stop_ffmpeg()
            self._state, self._video_id, self._started_at, self._error = StreamState.STOPPED, None, None, None
        return self.status()

    def status(self) -> StreamStatusOut:
        with self._lock:
            state, video_id, started_at, error = self._state, self._video_id, self._started_at, self._error
            managed = self._mediamtx is not None and self._mediamtx.poll() is None
        mediamtx_up = managed or self._reachable(self.rtsp_url, 0.3)
        return StreamStatusOut(
            state=state.value,
            video_id=video_id,
            started_at=started_at,
            uptime_seconds=round(time.time() - started_at, 1) if state is StreamState.RUNNING and started_at else None,
            rtsp_url=self.rtsp_url,
            error=error,
            mediamtx="running" if mediamtx_up else "not running",
            mediamtx_managed=managed,
        )

    def shutdown(self) -> None:
        """Stop ffmpeg and the MediaMTX this backend started."""
        with self._operation, self._lock:
            self._stop_ffmpeg()
            self._state = StreamState.STOPPED
            mediamtx, self._mediamtx = self._mediamtx, None
        if mediamtx is not None:
            _terminate(mediamtx)
            logger.info("MediaMTX stopped")

    # ------------------------------------------------------------------ internals

    def _spawn(self, args: list[str], name: str, **kwargs: Any) -> subprocess.Popen[Any]:
        """Start a guarded child process whose output is logged by a watcher thread."""
        process = self._popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **self._guard.popen_kwargs(),
            **kwargs,
        )
        self._guard.adopt(process)
        threading.Thread(target=self._watch, args=(process, name), name=f"watch-{name}", daemon=True).start()
        return process

    def _watch(self, process: subprocess.Popen[Any], name: str) -> None:
        """Log a child's output and notice when it exits."""
        for raw_line in iter(process.stdout.readline, ""):  # type: ignore[union-attr]
            line = raw_line.rstrip()
            if not line:
                continue
            if name == "ffmpeg":
                self._ffmpeg_tail.append(line)
                logger.warning("ffmpeg: {}", line)
            elif " ERR " in line:
                logger.error("mediamtx: {}", line)
            elif " WAR " in line:
                logger.warning("mediamtx: {}", line)
            else:
                logger.debug("mediamtx: {}", line)
        code = process.wait()
        with self._lock:
            if name == "ffmpeg" and process is self._ffmpeg:  # it was not stopped on purpose
                self._ffmpeg = None
                last = " | ".join(list(self._ffmpeg_tail)[-3:])
                self._fail(f"ffmpeg exited with code {code}" + (f": {last}" if last else ""))
            elif name == "mediamtx" and process is self._mediamtx:
                self._mediamtx = None
                logger.error("MediaMTX exited unexpectedly with code {}", code)

    def _stop_ffmpeg(self) -> None:
        process, self._ffmpeg = self._ffmpeg, None  # cleared first: the watcher sees a planned stop
        if process is not None:
            _terminate(process)

    def _fail(self, message: str) -> None:
        with self._lock:
            self._state, self._error = StreamState.ERROR, message
        logger.error("Stream of {} failed: {}", self._video_id, message)
