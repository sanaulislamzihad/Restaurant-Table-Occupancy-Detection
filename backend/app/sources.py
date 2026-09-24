"""Frame source that hides where frames come from.

``FrameSource`` accepts a video file path, a stream URL (``rtsp://``,
``http://`` ...) or a webcam index such as ``"0"``. A background thread reads
frames as fast as the source delivers them and keeps only the newest one, so a
slow consumer never falls behind a live stream. Streams and webcams reconnect
by themselves when they drop. Files play at their own frame rate, the way a
camera would deliver them, and can loop forever.
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import cv2
import numpy as np
from loguru import logger

if TYPE_CHECKING:
    from app.settings import Settings

# OpenCV's FFmpeg backend reads its options from this variable when a capture
# opens. RTSP over TCP avoids the packet loss (and grey smearing) of UDP.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

FALLBACK_FILE_FPS = 25.0  # used when a file does not report its frame rate
_DEFAULT_PORTS = {"rtsp": 554, "rtsps": 322, "http": 80, "https": 443}


class SourceStatus(str, Enum):
    """Connection state of a FrameSource."""

    CONNECTING = "connecting"      # opening for the first time
    LIVE = "live"                  # frames are arriving
    RECONNECTING = "reconnecting"  # lost or could not open; retrying
    ENDED = "ended"                # a file finished (or cannot be opened); no more frames
    STOPPED = "stopped"            # release() was called


class _Kind(str, Enum):
    FILE = "file"
    STREAM = "stream"
    WEBCAM = "webcam"


def _detect_kind(source: str) -> _Kind:
    if source.isdigit():
        return _Kind.WEBCAM
    if "://" in source:
        return _Kind.STREAM
    return _Kind.FILE


def redact(source: str) -> str:
    """Hide the password in URLs such as rtsp://user:pass@host/ before logging them."""
    return re.sub(r"(://[^:/@]+):[^@/]*@", r"\1:***@", source)


def _is_reachable(url: str, timeout: float) -> bool:
    """Quick TCP check of a stream URL's host and port.

    On Windows, FFmpeg does not notice a refused connection and waits for its
    whole open timeout, so a plain socket check first keeps retries fast while
    a camera or server is down.
    """
    try:
        parts = urlsplit(url)
        port = parts.port or _DEFAULT_PORTS.get(parts.scheme.lower())
    except ValueError:  # malformed port: let OpenCV report the problem
        return True
    if not parts.hostname or port is None:
        return True
    try:
        with socket.create_connection((parts.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


class FrameSource:
    """Latest-frame reader for a file, a stream URL or a webcam.

    ``read()`` returns the newest frame that has not been returned yet.
    ``is_alive()`` stays true while the source is connected or still retrying;
    it turns false when a file has ended or after ``release()``.
    """

    def __init__(
        self,
        source: str,
        *,
        reconnect_seconds: float = 3.0,
        open_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 10.0,
        loop_files: bool = True,
    ) -> None:
        self.source = source.strip()
        self._label = redact(self.source)
        self._kind = _detect_kind(self.source)
        self._reconnect_seconds = reconnect_seconds
        self._open_timeout_ms = int(open_timeout_seconds * 1000)
        self._read_timeout_ms = int(read_timeout_seconds * 1000)
        self._loop_files = loop_files

        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._frame_seq = 0  # increases with every published frame
        self._read_seq = 0   # sequence number of the last frame returned by read()
        self._status = SourceStatus.CONNECTING
        self._fps = 0.0
        self._resolution: tuple[int, int] | None = None

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"FrameSource {self._label}", daemon=True)
        self._thread.start()

    @classmethod
    def from_settings(cls, source: str, settings: Settings) -> FrameSource:
        """Create a source with the reconnect/timeout/loop values from the settings."""
        return cls(
            source,
            reconnect_seconds=settings.source_reconnect_seconds,
            open_timeout_seconds=settings.source_open_timeout_seconds,
            read_timeout_seconds=settings.source_read_timeout_seconds,
            loop_files=settings.loop_video_files,
        )

    # ------------------------------------------------------------------ public API

    def read(self, timeout: float | None = 1.0) -> tuple[bool, np.ndarray | None]:
        """Return ``(True, frame)`` with the newest unseen frame, else ``(False, None)``.

        Waits up to ``timeout`` seconds for a new frame (``None`` waits forever,
        until a frame arrives or the source stops).
        """
        with self._cond:
            self._cond.wait_for(
                lambda: self._frame_seq > self._read_seq
                or self._status in (SourceStatus.ENDED, SourceStatus.STOPPED),
                timeout,
            )
            if self._frame is None or self._frame_seq <= self._read_seq:
                return False, None
            self._read_seq = self._frame_seq
            return True, self._frame

    def is_alive(self) -> bool:
        """True while frames are arriving or the source is still trying to connect."""
        return self._thread.is_alive() and not self._stop.is_set()

    def release(self) -> None:
        """Stop the reader thread and close the source. Safe to call more than once."""
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            # A blocked open/read returns within its timeout.
            self._thread.join(timeout=(self._open_timeout_ms + self._read_timeout_ms) / 1000 + 1)
            if self._thread.is_alive():
                logger.warning("Reader thread for {} did not stop in time", self._label)
        with self._cond:
            self._status = SourceStatus.STOPPED
            self._frame = None
            self._cond.notify_all()

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def fps(self) -> float:
        """Frame rate reported by the source (0.0 if unknown)."""
        return self._fps

    @property
    def resolution(self) -> tuple[int, int] | None:
        """(width, height) of the latest frame, or None before the first frame."""
        return self._resolution

    def __enter__(self) -> FrameSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    # ------------------------------------------------------------------ reader thread

    def _run(self) -> None:
        cap: cv2.VideoCapture | None = None
        frames_since_open = 0
        next_frame_at = 0.0
        try:
            while not self._stop.is_set():
                try:
                    if cap is None:
                        cap = self._open()
                        if cap is None:
                            if self._kind is _Kind.FILE:
                                self._set_status(SourceStatus.ENDED)
                                return
                            self._retry_later(f"Could not open {self._label}")
                            continue
                        frames_since_open = 0
                        next_frame_at = time.monotonic()

                    ok, frame = cap.read()
                    if not ok or frame is None:
                        cap.release()
                        cap = None
                        if self._kind is _Kind.FILE:
                            if self._loop_files and frames_since_open > 0:
                                logger.debug("{} ended, starting it again", self._label)
                                continue
                            logger.info("{} ended", self._label)
                            self._set_status(SourceStatus.ENDED)
                            return
                        self._retry_later(f"Lost {self._label}")
                        continue

                    frames_since_open += 1
                    if self._kind is _Kind.FILE:
                        next_frame_at = self._wait_for_frame_time(next_frame_at)
                    self._publish(frame)
                except Exception:  # the reader must never die on an unexpected error
                    logger.exception("Error while reading {}", self._label)
                    if cap is not None:
                        cap.release()
                        cap = None
                    self._retry_later(f"Restarting {self._label}")
        finally:
            if cap is not None:
                cap.release()

    def _open(self) -> cv2.VideoCapture | None:
        """Open the capture, or return None if that fails."""
        if self._kind is _Kind.WEBCAM:
            cap = cv2.VideoCapture(int(self.source))
        elif self._kind is _Kind.STREAM:
            if not _is_reachable(self.source, self._open_timeout_ms / 1000):
                return None
            # Timeouts keep a dead or unreachable camera from blocking forever.
            cap = cv2.VideoCapture(
                self.source,
                cv2.CAP_FFMPEG,
                [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self._open_timeout_ms,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, self._read_timeout_ms,
                ],
            )
        else:
            if not Path(self.source).is_file():
                logger.error("Video file not found: {}", self.source)
                return None
            cap = cv2.VideoCapture(self.source)

        if not cap.isOpened():
            cap.release()
            return None
        fps = cap.get(cv2.CAP_PROP_FPS)
        if 0 < fps < 1000:
            self._fps = fps
        elif self._kind is _Kind.FILE:
            self._fps = FALLBACK_FILE_FPS
        logger.info("Opened {} ({:.1f} fps)", self._label, self._fps)
        return cap

    def _wait_for_frame_time(self, next_frame_at: float) -> float:
        """Sleep until a file frame is due, so files play at their own frame rate."""
        next_frame_at += 1.0 / (self._fps or FALLBACK_FILE_FPS)
        delay = next_frame_at - time.monotonic()
        if delay > 0:
            self._stop.wait(delay)
        elif delay < -1.0:  # fell far behind (e.g. the machine was busy): start over
            next_frame_at = time.monotonic()
        return next_frame_at

    def _publish(self, frame: np.ndarray) -> None:
        with self._cond:
            if self._stop.is_set():
                return
            if self._status is not SourceStatus.LIVE:
                logger.info("{} is live", self._label)
            self._frame = frame
            self._frame_seq += 1
            self._resolution = (frame.shape[1], frame.shape[0])
            self._status = SourceStatus.LIVE
            self._cond.notify_all()

    def _retry_later(self, reason: str) -> None:
        logger.warning("{}; retrying in {:g} s", reason, self._reconnect_seconds)
        self._set_status(SourceStatus.RECONNECTING)
        self._stop.wait(self._reconnect_seconds)

    def _set_status(self, status: SourceStatus) -> None:
        with self._cond:
            if not self._stop.is_set():
                self._status = status
            self._cond.notify_all()

