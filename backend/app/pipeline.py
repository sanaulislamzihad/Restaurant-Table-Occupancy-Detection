"""Live pipeline: frames in; annotated JPEGs, table status and events out.

Two background threads share the latest data:

* The frame thread reads every frame from the source, draws the latest
  detection results on it and keeps the newest JPEG for the MJPEG stream, so
  the video stays smooth at the camera's frame rate.
* The detection thread takes the newest frame, runs person detection and
  tracking, updates table occupancy, stores status changes in SQLite and
  pushes status and events to WebSocket clients.

The pipeline never knows whether frames come from a file, an RTSP camera or a
webcam: it only gets frames from FrameSource.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

import cv2
import numpy as np
import supervision as sv
from loguru import logger
from pydantic import ValidationError

from app.annotator import FrameAnnotator, source_label
from app.broadcaster import Broadcaster
from app.config_store import ConfigStore, default_occupancy
from app.db import Database
from app.detector import PersonDetector
from app.geometry import outline_problem
from app.occupancy import OccupancyTracker, TableState
from app.schemas import (
    EventOut,
    PipelineStatus,
    ReferencePoint,
    TableConfig,
    TableDef,
    TablesUpdate,
    TableStatusOut,
)
from app.settings import Settings
from app.sources import FrameSource, SourceStatus, redact

STATUS_HEARTBEAT_SECONDS = 1.0  # status is pushed at least this often
FPS_LOG_SECONDS = 10.0
IDLE_REDRAW_SECONDS = 0.5  # while no frames arrive, redraw the last one so the overlay shows the source status
PLACEHOLDER_SHAPE = (360, 640, 3)  # image shown before the first frame arrives


class Detector(Protocol):
    """What the pipeline needs from a person detector (PersonDetector, or a fake in tests)."""

    device: str
    confidence: float

    def detect(self, frame: np.ndarray) -> sv.Detections: ...

    def reset_tracking(self) -> None: ...


class ConfigError(ValueError):
    """A table config change was rejected; the message says why."""


@dataclass(frozen=True)
class Results:
    """Latest detection results, shared with the frame thread and the API."""

    tables: list[TableDef] = field(default_factory=list)  # scaled to the live frame size
    states: list[TableState] = field(default_factory=list)  # copies, safe to read anywhere
    detections: sv.Detections = field(default_factory=sv.Detections.empty)
    reference_point: ReferencePoint = "bottom_center"


class RateMeter:
    """Events per second over the last few seconds (thread-safe)."""

    def __init__(self, window_seconds: float = 3.0) -> None:
        self._window = window_seconds
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._times.append(now)
            while self._times and now - self._times[0] > self._window:
                self._times.popleft()

    @property
    def rate(self) -> float:
        with self._lock:
            if len(self._times) < 2 or time.monotonic() - self._times[-1] > self._window:
                return 0.0
            return (len(self._times) - 1) / (self._times[-1] - self._times[0])


class Pipeline:
    """Background processing of the active video; see the module docstring."""

    def __init__(
        self,
        settings: Settings,
        store: ConfigStore,
        db: Database,
        broadcaster: Broadcaster,
        detector_factory: Callable[[Settings], Detector] = PersonDetector.from_settings,
    ) -> None:
        self.settings = settings
        self._store = store
        self._db = db
        self._broadcaster = broadcaster
        self._detector_factory = detector_factory
        self._annotator = FrameAnnotator()

        self._lock = threading.RLock()  # guards everything below
        self._frame_ready = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        # What is being watched. _generation changes when the video changes.
        self._video_id: str | None = None
        self._config: TableConfig | None = None
        self._source: FrameSource | None = None
        self._generation = 0

        # Latest data.
        self._frame: np.ndarray | None = None
        self._frame_seq = 0
        self._results = Results()
        self._jpeg: bytes | None = None
        self._jpeg_seq = 0

        self._detector: Detector | None = None
        self._model_state = "loading"
        self._processing = RateMeter()
        self._streaming = RateMeter()
        self._last_status_key: object = None
        self._last_status_sent = 0.0

    # ------------------------------------------------------------------ lifecycle

    def start(self, video_id: str | None = None) -> None:
        """Start watching video_id (None: the fake camera with no tables) and start both threads."""
        self.activate(video_id)
        for target, name in ((self._frame_loop, "pipeline-frames"), (self._detection_loop, "pipeline-detection")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        """Stop both threads and close the source."""
        self._stop.set()
        with self._lock:
            self._frame_ready.notify_all()
            source, self._source = self._source, None
        if source is not None:
            source.release()
        for thread in self._threads:
            thread.join(timeout=10)
        self._threads.clear()
        logger.info("Pipeline stopped")

    @property
    def running(self) -> bool:
        return not self._stop.is_set() and any(thread.is_alive() for thread in self._threads)

    @property
    def video_id(self) -> str | None:
        return self._video_id

    @property
    def model_state(self) -> str:
        """Detection model state: loading, ready or failed."""
        return self._model_state

    @property
    def device(self) -> str | None:
        with self._lock:
            return self._detector.device if self._detector else None

    @property
    def source_label(self) -> str:
        """LIVE, RECONNECTING or NO SOURCE."""
        with self._lock:
            return source_label(self._source.status if self._source else SourceStatus.STOPPED)

    @property
    def processing_fps(self) -> float:
        return round(self._processing.rate, 1)

    @property
    def stream_fps(self) -> float:
        return round(self._streaming.rate, 1)

    # ------------------------------------------------------------------ what to watch

    def activate(self, video_id: str | None) -> None:
        """Watch a video: load its table config and switch to its source.

        Without a config (or with video_id None) the fake camera URL is watched
        with no tables. When the video changes, the source is reopened so no
        stale frame of the previous video is processed with the new tables.
        """
        config = None
        if video_id:
            try:
                config = self._store.load(video_id)
            except (ValidationError, ValueError) as error:
                logger.error("Table config of {} is invalid: {}", video_id, error)
        source_url = (config.source if config else self.settings.fake_camera_rtsp_url).strip()

        with self._lock:
            old_source = None
            if self._source is None or self._source.source != source_url or video_id != self._video_id:
                old_source = self._source
                self._source = FrameSource.from_settings(source_url, self.settings)
                self._frame = None
            self._video_id, self._config = video_id, config
            self._generation += 1
            self._results = Results()
        if old_source is not None:
            # Closing can wait for a blocked read; do not hold up the caller.
            threading.Thread(target=old_source.release, name="release-old-source", daemon=True).start()
        tables = f"{len(config.tables)} tables" if config else "no tables"
        logger.info("Watching {} from {} ({})", video_id or "no video", redact(source_url), tables)
        self._publish_status(force=True)

    def current_config(self) -> TableConfig | None:
        with self._lock:
            return self._config

    def update_tables(self, update: TablesUpdate) -> TableConfig:
        """Save new table outlines for the active video and use them right away."""
        with self._lock:
            video_id, config, frame = self._video_id, self._config, self._frame
            source_url = self._source.source if self._source else self.settings.fake_camera_rtsp_url
        if video_id is None:
            raise ConfigError("No video is active. Start a stream first.")

        width = update.frame_width or (frame.shape[1] if frame is not None else config.frame_width if config else None)
        height = update.frame_height or (frame.shape[0] if frame is not None else config.frame_height if config else None)
        if width is None or height is None:
            raise ConfigError("No frame received yet: send frame_width and frame_height with the tables.")
        problems = [
            f"{table.name}: {problem}"
            for table in update.tables
            if (problem := outline_problem(table.polygon, (width, height)))
        ]
        if problems:
            raise ConfigError("Unusable table outline. " + "; ".join(problems))
        try:
            new_config = TableConfig(
                video_id=video_id,
                source=config.source if config else source_url,
                frame_width=width,
                frame_height=height,
                tables=update.tables,
                occupancy=config.occupancy if config else default_occupancy(self.settings),
            )
        except ValidationError as error:
            raise ConfigError(str(error)) from error

        self._store.save(new_config)
        with self._lock:
            if self._video_id == video_id:
                self._config = new_config  # the detection thread picks it up on its next frame
        logger.info("Saved {} tables for {}", len(new_config.tables), video_id)
        return new_config

    # ------------------------------------------------------------------ data for the API

    def latest_jpeg(self) -> tuple[int, bytes | None]:
        """(sequence number, newest annotated JPEG)."""
        with self._lock:
            return self._jpeg_seq, self._jpeg

    def snapshot_jpeg(self) -> bytes | None:
        """The newest raw (not annotated) frame as a JPEG, or None before the first frame."""
        with self._lock:
            frame = self._frame
        if frame is None:
            return None
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        return buffer.tobytes() if ok else None

    def status(self) -> PipelineStatus:
        """Current table status and pipeline numbers."""
        with self._lock:
            results, video_id, config, frame = self._results, self._video_id, self._config, self._frame
            source_status = self._source.status if self._source else SourceStatus.STOPPED
        now = time.time()
        tables = [
            TableStatusOut(
                id=state.table_id,
                name=state.name,
                status=state.status.value,
                occupied=state.status.counts_as_occupied,
                people_count=state.people_count,
                track_ids=list(state.track_ids),
                occupied_since=state.occupied_since,
                current_session_seconds=round(state.occupied_seconds(now) - state.total_occupied_seconds, 1),
                total_occupied_seconds=round(state.occupied_seconds(now), 1),
                session_count=state.session_count,
            )
            for state in results.states
        ]
        return PipelineStatus(
            video_id=video_id,
            has_tables=bool(config and config.tables),
            source_status=source_label(source_status),
            processing_fps=round(self._processing.rate, 1),
            stream_fps=round(self._streaming.rate, 1),
            frame_width=frame.shape[1] if frame is not None else None,
            frame_height=frame.shape[0] if frame is not None else None,
            people_count=len(results.detections),
            table_count=len(tables),
            available_count=sum(not table.occupied for table in tables),
            tables=tables,
            timestamp=now,
        )

    # ------------------------------------------------------------------ frame thread

    def _frame_loop(self) -> None:
        last_render = 0.0
        while not self._stop.is_set():
            try:
                with self._lock:
                    source = self._source
                frame = None
                if source is None:
                    self._stop.wait(IDLE_REDRAW_SECONDS)
                else:
                    ok, frame = source.read(timeout=IDLE_REDRAW_SECONDS)
                    if not ok:
                        frame = None

                if frame is not None:
                    with self._frame_ready:
                        if self._source is not source:  # the video changed while reading
                            continue
                        self._frame = frame
                        self._frame_seq += 1
                        self._frame_ready.notify_all()
                    self._render(frame)
                    self._streaming.tick()
                    last_render = time.monotonic()
                elif time.monotonic() - last_render >= IDLE_REDRAW_SECONDS:
                    with self._lock:
                        last_frame = self._frame
                    self._render(last_frame)  # keeps the overlay (RECONNECTING, clock) up to date
                    last_render = time.monotonic()
                self._publish_status()
            except Exception:  # keep streaming whatever happens to one frame
                logger.exception("Frame thread error")
                self._stop.wait(1.0)

    def _render(self, frame: np.ndarray | None) -> None:
        """Draw the latest results on a frame (or a blank image) and keep it as the stream JPEG."""
        with self._lock:
            results = self._results
            source_status = self._source.status if self._source else SourceStatus.STOPPED
        if frame is None:
            frame, results = np.zeros(PLACEHOLDER_SHAPE, np.uint8), Results()
        image = self._annotator.annotate(
            frame,
            results.detections,
            results.tables,
            results.states,
            reference_point=results.reference_point,
            processing_fps=self._processing.rate,
            source_status=source_status,
        )
        ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.settings.stream_jpeg_quality])
        if ok:
            with self._lock:
                self._jpeg = buffer.tobytes()
                self._jpeg_seq += 1

    # ------------------------------------------------------------------ detection thread

    def _detection_loop(self) -> None:
        try:
            detector = self._detector_factory(self.settings)
        except Exception:
            logger.exception("Could not load the detection model")
            self._model_state = "failed"
            return
        with self._lock:
            self._detector = detector
        self._model_state = "ready"

        tracker: OccupancyTracker | None = None
        tracker_config: TableConfig | None = None
        tracker_size: tuple[int, int] | None = None
        generation = -1
        seen_seq = frames_waiting = 0
        last_fps_log = time.monotonic()

        while not self._stop.is_set():
            with self._frame_ready:
                self._frame_ready.wait_for(lambda: self._frame_seq != seen_seq or self._stop.is_set(), timeout=0.5)
                if self._stop.is_set() or self._frame_seq == seen_seq or self._frame is None:
                    continue
                frames_waiting += self._frame_seq - seen_seq
                seen_seq = self._frame_seq
                if frames_waiting < self.settings.detect_every_n_frames:
                    continue
                frames_waiting = 0
                frame, config, video_id, current_generation = (
                    self._frame, self._config, self._video_id, self._generation,
                )
            try:
                if current_generation != generation:  # another video: start tracking from scratch
                    generation, tracker, tracker_config, tracker_size = current_generation, None, None, None
                    detector.reset_tracking()

                size = (frame.shape[1], frame.shape[0])
                if config is None or not config.tables:
                    tracker = tracker_config = None
                elif config is not tracker_config or size != tracker_size:  # new outlines or frame size
                    tables = config.tables_for_frame(*size)
                    if tracker is None:
                        tracker = OccupancyTracker(tables, config.occupancy)
                    else:
                        tracker.set_tables(tables, config.occupancy)
                    tracker_config, tracker_size = config, size

                detector.confidence = (
                    config.occupancy.confidence_threshold if config else self.settings.confidence_threshold
                )
                detections = detector.detect(frame)
                events = tracker.update(detections) if tracker else []
                self._processing.tick()

                results = Results(
                    tables=list(tracker.tables) if tracker else [],
                    states=[dataclasses.replace(state) for state in tracker.states] if tracker else [],
                    detections=detections,
                    reference_point=config.occupancy.reference_point if config else self.settings.reference_point,
                )
                with self._lock:
                    if self._generation != current_generation:
                        continue  # the video changed during detection: drop these results
                    self._results = results
                if events:
                    self._record_events(video_id, events, {table.id: table.name for table in results.tables})
                self._publish_status(force=bool(events))

                if time.monotonic() - last_fps_log >= FPS_LOG_SECONDS:
                    last_fps_log = time.monotonic()
                    logger.info("Processing {:.1f} fps, streaming {:.1f} fps, {} people",
                                self._processing.rate, self._streaming.rate, len(detections))
            except Exception:
                logger.exception("Detection failed on a frame")
                self._stop.wait(1.0)

    def _record_events(self, video_id: str | None, events: list, table_names: dict[str, str]) -> None:
        """Store status changes and push them to WebSocket clients."""
        try:
            rows = self._db.add_events(video_id, events, table_names)
        except Exception:
            logger.exception("Could not store {} events", len(events))
            return
        for row in rows:
            logger.info("{} became {}", row["table_name"], row["new_status"])
            self._broadcaster.publish({"type": "event", "data": EventOut(**row).model_dump(mode="json")})

    # ------------------------------------------------------------------ status push

    def _publish_status(self, force: bool = False) -> None:
        """Push the status to WebSocket clients when it changed, and at least once a second."""
        now = time.monotonic()
        with self._lock:
            results = self._results
            key = (
                self._video_id,
                self._source.status if self._source else None,
                tuple((state.table_id, state.status, state.people_count) for state in results.states),
                len(results.detections),
            )
            if not force and key == self._last_status_key and now - self._last_status_sent < STATUS_HEARTBEAT_SECONDS:
                return
            self._last_status_key, self._last_status_sent = key, now
        self._broadcaster.publish({"type": "status", "data": self.status().model_dump(mode="json")})
