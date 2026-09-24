"""Tests for a video source that stops sending frames and comes back."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.analytics import rebuild_sessions
from app.broadcaster import Broadcaster
from app.config_store import ConfigStore
from app.db import Database
from app.pipeline import Pipeline
from app.schemas import OccupancySettings, TableConfig, TableDef
from app.sources import SourceStatus
from conftest import FakeDetector, make_settings


class SwitchableSource:
    """Sends frames while ``sending`` is true, like a camera that can drop out."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.sending = True

    @property
    def status(self) -> SourceStatus:
        return SourceStatus.LIVE if self.sending else SourceStatus.RECONNECTING

    def read(self, timeout: float = 1.0) -> tuple[bool, np.ndarray | None]:
        if not self.sending:
            time.sleep(min(timeout, 0.05))
            return False, None
        time.sleep(0.02)
        return True, np.zeros((180, 320, 3), np.uint8)

    def release(self) -> None:
        self.sending = False


def wait_until(condition: Callable[[], Any], timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := condition():
            return result
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def test_an_outage_ends_the_run_and_occupancy_starts_over(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, source_outage_seconds=0.5)
    store = ConfigStore(settings.configs_dir)
    store.save(TableConfig(
        video_id="cam", source="rtsp://camera.local/stream", frame_width=320, frame_height=180,
        tables=[TableDef(id="T1", name="Table 1", polygon=[(20, 20), (150, 20), (150, 170), (20, 170)])],
        occupancy=OccupancySettings(confidence_threshold=0.1, enter_seconds=0, leave_seconds=10),
    ))
    db = Database(tmp_path / "app.db")
    sources: list[SwitchableSource] = []

    def make_source(url: str, _settings: object) -> SwitchableSource:
        sources.append(SwitchableSource(url))
        return sources[-1]

    pipeline = Pipeline(settings, store, db, Broadcaster(), detector_factory=lambda _s: FakeDetector(),
                        source_factory=make_source)
    pipeline.start("cam")
    try:
        occupied = lambda: [t.status for t in pipeline.status().tables] == ["OCCUPIED"]  # noqa: E731
        wait_until(occupied)
        first_run = pipeline._run
        time.sleep(0.3)  # the guests stay a moment

        sources[0].sending = False  # the camera drops out
        wait_until(lambda: pipeline._run is None)
        [(start, end)] = db.runs_between("cam", 0, time.time(), time.time())
        assert end - start < 5 and time.time() - end >= 0.5  # ended at the last frame, not now

        sources[0].sending = True  # and comes back
        wait_until(lambda: pipeline._run is not None)
        assert pipeline._run != first_run
        wait_until(occupied)
        runs = db.runs_between("cam", 0, time.time(), time.time())
        assert len(runs) == 2 and runs[1][0] >= runs[0][1] + 0.5  # the outage is not watched time

        events = db.events_between("cam", 0, time.time())
        assert [e["new_status"] for e in events] == ["PENDING_OCCUPIED", "OCCUPIED"] * 2  # started over
        sessions = rebuild_sessions(events, runs)
        assert len(sessions) == 2 and sessions[0].end == runs[0][1]
    finally:
        pipeline.stop()
        db.close()
