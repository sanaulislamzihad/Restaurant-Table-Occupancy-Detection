"""Video library: uploads, metadata, thumbnails and deletion.

Videos are stored in VIDEOS_DIR, their metadata in SQLite and a thumbnail of
the first frame in DATA_DIR/thumbnails. Video files that are already in
VIDEOS_DIR when the backend starts (copied there by hand) are added too, with
their file name as ID, so an existing configs/<name>.json applies to them.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
from fastapi import UploadFile
from loguru import logger

from app.config_store import ConfigStore
from app.db import Database
from app.process_guard import ChildProcessGuard
from app.schemas import ID_PATTERN
from app.settings import Settings
from app.stream_manager import resolve_executable

ALLOWED_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv")
CHUNK_SIZE = 1024 * 1024  # uploads are copied to disk 1 MB at a time
THUMBNAIL_WIDTH = 480
PROBE_TIMEOUT_S = 30


class VideoError(Exception):
    """An upload was rejected; status_code is the HTTP status to answer with."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class VideoInfo:
    duration_seconds: float | None
    width: int
    height: int
    fps: float | None
    codec: str | None


def _parse_rate(rate: str | None) -> float | None:
    """ffprobe frame rates look like "30000/1001"."""
    try:
        value = float(Fraction(rate)) if rate else 0.0
    except (ValueError, ZeroDivisionError):
        return None
    return round(value, 3) if value > 0 else None


def probe_video(path: Path, ffprobe: str | None) -> VideoInfo | None:
    """Metadata of a video (ffprobe, or OpenCV if ffprobe is missing), or None if it is not a video."""
    if ffprobe:
        command = [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,avg_frame_rate:format=duration",
            "-of", "json", str(path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
                                    **ChildProcessGuard.popen_kwargs())
            data = json.loads(result.stdout or "{}")
            streams = data.get("streams") or []
            if result.returncode != 0 or not streams:
                return None
            stream = streams[0]
            duration = data.get("format", {}).get("duration")
            return VideoInfo(
                duration_seconds=round(float(duration), 2) if duration else None,
                width=int(stream["width"]),
                height=int(stream["height"]),
                fps=_parse_rate(stream.get("avg_frame_rate")),
                codec=stream.get("codec_name"),
            )
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as error:
            logger.warning("ffprobe failed on {} ({}); reading it with OpenCV", path.name, error)

    capture = cv2.VideoCapture(str(path))
    try:
        ok, frame = capture.read() if capture.isOpened() else (False, None)
        if not ok or frame is None:
            return None
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        return VideoInfo(
            duration_seconds=round(frames / fps, 2) if fps > 0 and frames > 0 else None,
            width=frame.shape[1],
            height=frame.shape[0],
            fps=round(fps, 3) if fps > 0 else None,
            codec=None,
        )
    finally:
        capture.release()


def read_frame(video: Path, at_seconds: float = 0.0) -> np.ndarray | None:
    """The frame at ``at_seconds`` (the first frame if the video is shorter), or None."""
    capture = cv2.VideoCapture(str(video))
    try:
        if not capture.isOpened():
            return None
        if at_seconds > 0:
            capture.set(cv2.CAP_PROP_POS_MSEC, at_seconds * 1000)
            ok, frame = capture.read()
            if ok and frame is not None:
                return frame
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = capture.read()
        return frame if ok else None
    finally:
        capture.release()


def make_thumbnail(video: Path, destination: Path) -> bool:
    """Save the first frame, at most THUMBNAIL_WIDTH wide, as a JPEG."""
    capture = cv2.VideoCapture(str(video))
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        return False
    height, width = frame.shape[:2]
    if width > THUMBNAIL_WIDTH:
        frame = cv2.resize(frame, (THUMBNAIL_WIDTH, round(height * THUMBNAIL_WIDTH / width)),
                           interpolation=cv2.INTER_AREA)
    encoded, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not encoded:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(buffer.tobytes())  # works with any characters in the path
    return True


class VideoLibrary:
    """Uploaded videos on disk and in the database."""

    def __init__(self, settings: Settings, db: Database, store: ConfigStore) -> None:
        self.settings = settings
        self.videos_dir = settings.videos_dir
        self.thumbnails_dir = settings.data_dir / "thumbnails"
        self.max_bytes = settings.max_upload_mb * 1024 * 1024
        self._db = db
        self._store = store
        self._ffprobe = resolve_executable(settings.ffprobe_path)
        if self._ffprobe is None:
            logger.warning("ffprobe not found ('{}'); video metadata will be read with OpenCV",
                           settings.ffprobe_path)
        self.videos_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ queries

    def list(self) -> list[dict]:
        return self._db.list_videos()

    def get(self, video_id: str) -> dict | None:
        return self._db.get_video(video_id)

    def path(self, video_id: str) -> Path | None:
        video = self.get(video_id)
        return self.videos_dir / video["filename"] if video else None

    def thumbnail_path(self, video_id: str) -> Path | None:
        path = self.thumbnails_dir / f"{video_id}.jpg"
        return path if path.is_file() else None

    # ------------------------------------------------------------------ changes

    async def save_upload(self, upload: UploadFile) -> dict:
        """Copy an upload to VIDEOS_DIR in chunks, check it is a video, and register it."""
        original_name = Path(upload.filename or "").name
        extension = Path(original_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise VideoError(415, f"Unsupported file type '{extension or 'none'}'. "
                                  f"Allowed: {', '.join(e.lstrip('.') for e in ALLOWED_EXTENSIONS)}.")
        video_id = secrets.token_hex(6)
        final_path = self.videos_dir / f"{video_id}{extension}"
        partial_path = final_path.with_name(final_path.name + ".part")
        size = 0
        try:
            with partial_path.open("wb") as out:
                while chunk := await upload.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise VideoError(413, f"The file is larger than {self.settings.max_upload_mb} MB.")
                    out.write(chunk)
            if size == 0:
                raise VideoError(400, "The file is empty.")
            info = await asyncio.to_thread(probe_video, partial_path, self._ffprobe)
            if info is None:
                raise VideoError(422, "This file could not be read as a video.")
            os.replace(partial_path, final_path)
        except BaseException:
            partial_path.unlink(missing_ok=True)
            raise
        await asyncio.to_thread(make_thumbnail, final_path, self.thumbnails_dir / f"{video_id}.jpg")
        video = self._register(video_id, original_name, final_path, size, info)
        logger.info("Uploaded {} as {} ({:.1f} MB)", original_name, video_id, size / 1e6)
        return video

    def import_existing(self) -> int:
        """Register video files that were put in VIDEOS_DIR by hand. Returns how many were added."""
        known = {video["filename"] for video in self.list()}
        added = 0
        for path in sorted(self.videos_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS or path.name in known:
                continue
            info = probe_video(path, self._ffprobe)
            if info is None:
                logger.warning("Skipping {}: not a readable video", path.name)
                continue
            video_id = self._id_for_file(path)
            make_thumbnail(path, self.thumbnails_dir / f"{video_id}.jpg")
            self._register(video_id, path.name, path, path.stat().st_size, info)
            logger.info("Added {} from the videos folder as {}", path.name, video_id)
            added += 1
        return added

    def delete(self, video_id: str) -> bool:
        """Remove a video's file, thumbnail, table config, database row and history."""
        video = self.get(video_id)
        if video is None:
            return False
        (self.videos_dir / video["filename"]).unlink(missing_ok=True)
        (self.thumbnails_dir / f"{video_id}.jpg").unlink(missing_ok=True)
        self._store.delete(video_id)
        self._db.delete_video(video_id)
        logger.info("Deleted video {} ({})", video_id, video["original_name"])
        return True

    # ------------------------------------------------------------------ internals

    def _register(self, video_id: str, original_name: str, path: Path, size: int, info: VideoInfo) -> dict:
        return self._db.add_video({
            "id": video_id,
            "original_name": original_name,
            "filename": path.name,
            "size_bytes": size,
            "duration_seconds": info.duration_seconds,
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "codec": info.codec,
            "uploaded_at": time.time(),
        })

    def _id_for_file(self, path: Path) -> str:
        """The file name without extension as ID when it is a valid, unused one."""
        candidate = re.sub(r"[^A-Za-z0-9_-]", "-", path.stem)[:64].strip("-")
        if re.fullmatch(ID_PATTERN, candidate) and self.get(candidate) is None:
            return candidate
        return secrets.token_hex(6)
