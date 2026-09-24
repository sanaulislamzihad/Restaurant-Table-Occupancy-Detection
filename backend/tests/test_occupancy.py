"""Tests for the table occupancy state machine, fed with fake detections."""

from __future__ import annotations

import numpy as np
import pytest
import supervision as sv

from app.occupancy import OccupancyEvent, OccupancyTracker, TableStatus
from app.schemas import OccupancySettings, TableConfig, TableDef

AVAILABLE = TableStatus.AVAILABLE
PENDING_OCCUPIED = TableStatus.PENDING_OCCUPIED
OCCUPIED = TableStatus.OCCUPIED
PENDING_AVAILABLE = TableStatus.PENDING_AVAILABLE

TABLE_1 = TableDef(id="T1", name="Table 1", polygon=[(100, 100), (200, 100), (200, 200), (100, 200)])
TABLE_2 = TableDef(id="T2", name="Table 2", polygon=[(300, 100), (400, 100), (400, 200), (300, 200)])
AT_TABLE_1 = (150, 150)
AT_TABLE_2 = (350, 150)
FAR_AWAY = (600, 400)


def settings(**overrides: object) -> OccupancySettings:
    values: dict[str, object] = {
        "confidence_threshold": 0.3,
        "enter_seconds": 5,
        "leave_seconds": 10,
        "reference_point": "bottom_center",
        "presence_hold_seconds": 1.0,
    }
    values.update(overrides)
    return OccupancySettings(**values)


def people(*feet: tuple[float, float], confidence: float = 0.9) -> sv.Detections:
    """People whose box bottom-centre (their feet) is at each given point; tracker IDs 1, 2, ..."""
    if not feet:
        return sv.Detections.empty()
    xyxy = np.array([[x - 10, y - 40, x + 10, y] for x, y in feet], dtype=float)
    return sv.Detections(
        xyxy=xyxy,
        confidence=np.full(len(feet), confidence),
        class_id=np.zeros(len(feet), dtype=int),
        tracker_id=np.arange(1, len(feet) + 1),
    )


def run(tracker: OccupancyTracker, detections: sv.Detections, start: float, end: float,
        step: float = 0.5) -> list[OccupancyEvent]:
    """Feed the same detections at every step from start up to (not including) end."""
    events = []
    for now in np.arange(start, end, step):
        events += tracker.update(detections, now=float(now))
    return events


def status(tracker: OccupancyTracker, table_id: str = "T1") -> TableStatus:
    return next(s for s in tracker.states if s.table_id == table_id).status


def test_enter_delay() -> None:
    tracker = OccupancyTracker([TABLE_1], settings())
    events = run(tracker, people(AT_TABLE_1), 0, 5)  # 0.0 ... 4.5 s
    assert status(tracker) is PENDING_OCCUPIED
    events += tracker.update(people(AT_TABLE_1), now=5.0)
    state = tracker.states[0]
    assert state.status is OCCUPIED
    assert state.occupied_since == 0.0 and state.session_count == 1
    assert [(e.old_status, e.new_status, e.timestamp) for e in events] == [
        (AVAILABLE, PENDING_OCCUPIED, 0.0),
        (PENDING_OCCUPIED, OCCUPIED, 5.0),
    ]


def test_leave_delay() -> None:
    tracker = OccupancyTracker([TABLE_1], settings())
    run(tracker, people(AT_TABLE_1), 0, 20)  # last seen at 19.5 s
    run(tracker, people(), 20, 21)  # still within the 1 s hold
    assert status(tracker) is OCCUPIED
    tracker.update(people(), now=21.0)
    assert status(tracker) is PENDING_AVAILABLE
    run(tracker, people(), 21.5, 29.5)
    assert status(tracker) is PENDING_AVAILABLE  # 9.5 s since last seen
    events = tracker.update(people(), now=29.5)  # 10 s since last seen
    state = tracker.states[0]
    assert state.status is AVAILABLE
    assert [(e.old_status, e.new_status) for e in events] == [(PENDING_AVAILABLE, AVAILABLE)]
    assert state.total_occupied_seconds == pytest.approx(19.5)  # arrival at 0 s -> last seen at 19.5 s
    assert state.occupied_since is None and state.session_count == 1


def test_returning_during_leave_delay_keeps_the_same_session() -> None:
    tracker = OccupancyTracker([TABLE_1], settings())
    run(tracker, people(AT_TABLE_1), 0, 10)
    run(tracker, people(), 10, 15)
    assert status(tracker) is PENDING_AVAILABLE
    run(tracker, people(AT_TABLE_1), 15, 16)
    state = tracker.states[0]
    assert state.status is OCCUPIED
    assert state.session_count == 1 and state.occupied_since == 0.0


def test_passer_by_is_ignored() -> None:
    tracker = OccupancyTracker([TABLE_1], settings())
    events = run(tracker, people(AT_TABLE_1), 0, 2)  # walks past for 2 s
    events += run(tracker, people(), 2, 20)
    state = tracker.states[0]
    assert state.status is AVAILABLE and state.session_count == 0
    assert OCCUPIED not in {e.new_status for e in events}
    assert [e.new_status for e in events] == [PENDING_OCCUPIED, AVAILABLE]


def test_multiple_tables_are_independent() -> None:
    tracker = OccupancyTracker([TABLE_1, TABLE_2], settings())
    run(tracker, people(AT_TABLE_1), 0, 3)
    run(tracker, people(AT_TABLE_1, AT_TABLE_2), 3, 6)
    assert status(tracker, "T1") is OCCUPIED  # present since 0 s
    assert status(tracker, "T2") is PENDING_OCCUPIED  # present since 3 s
    run(tracker, people(AT_TABLE_2), 6, 9)
    assert status(tracker, "T1") is PENDING_AVAILABLE
    assert status(tracker, "T2") is OCCUPIED


def test_people_count_and_track_ids() -> None:
    tracker = OccupancyTracker([TABLE_1, TABLE_2], settings())
    tracker.update(people(AT_TABLE_1, (160, 160), FAR_AWAY), now=0)
    table_1, table_2 = tracker.states
    assert table_1.people_count == 2 and table_1.track_ids == [1, 2]
    assert table_2.people_count == 0 and table_2.track_ids == []


def test_short_detection_gaps_do_not_reset_the_enter_timer() -> None:
    tracker = OccupancyTracker([TABLE_1], settings())
    for now in np.arange(0, 6, 0.5):
        seen = int(now * 2) % 3 != 1  # every third update misses the person
        tracker.update(people(AT_TABLE_1) if seen else people(), now=float(now))
    assert status(tracker) is OCCUPIED


def test_a_long_gap_resets_the_enter_timer() -> None:
    tracker = OccupancyTracker([TABLE_1], settings())
    run(tracker, people(AT_TABLE_1), 0, 3)
    run(tracker, people(), 3, 5)  # gone for 2 s > 1 s hold
    run(tracker, people(AT_TABLE_1), 5, 9)
    assert status(tracker) is PENDING_OCCUPIED  # the 5 s count restarted at 5 s


def test_low_confidence_detections_are_ignored() -> None:
    tracker = OccupancyTracker([TABLE_1], settings(confidence_threshold=0.5))
    tracker.update(people(AT_TABLE_1, confidence=0.4), now=0)
    assert status(tracker) is AVAILABLE and tracker.states[0].people_count == 0


def test_reference_point_choice() -> None:
    # Box from y=160 to y=240 at x=150: the centre (150, 200) is on the table edge
    # and the bottom centre (150, 240) is outside it.
    detections = sv.Detections(xyxy=np.array([[140.0, 160.0, 160.0, 240.0]]), tracker_id=np.array([7]))
    bottom = OccupancyTracker([TABLE_1], settings(reference_point="bottom_center", enter_seconds=0))
    center = OccupancyTracker([TABLE_1], settings(reference_point="center", enter_seconds=0))
    bottom.update(detections, now=0)
    center.update(detections, now=0)
    assert status(bottom) is AVAILABLE
    assert status(center) is OCCUPIED


def test_timing_is_wall_clock_not_frame_count() -> None:
    for step in (0.1, 0.5, 1.0):  # 10 fps, 2 fps, 1 fps
        tracker = OccupancyTracker([TABLE_1], settings())
        run(tracker, people(AT_TABLE_1), 0, 5, step=step)
        assert status(tracker) is PENDING_OCCUPIED
        tracker.update(people(AT_TABLE_1), now=5.0)
        assert status(tracker) is OCCUPIED


def test_occupied_seconds_includes_the_session_in_progress() -> None:
    tracker = OccupancyTracker([TABLE_1], settings())
    run(tracker, people(AT_TABLE_1), 0, 8)
    assert tracker.states[0].occupied_seconds(now=8.0) == pytest.approx(8.0)


def test_editing_tables_keeps_the_state_of_kept_tables() -> None:
    tracker = OccupancyTracker([TABLE_1, TABLE_2], settings())
    run(tracker, people(AT_TABLE_1), 0, 6)
    assert status(tracker, "T1") is OCCUPIED

    renamed = TABLE_1.model_copy(update={"name": "Window table"})
    table_3 = TableDef(id="T3", name="Table 3", polygon=[(500, 100), (600, 100), (600, 200), (500, 200)])
    tracker.set_tables([renamed, table_3], settings(enter_seconds=2))

    assert [s.table_id for s in tracker.states] == ["T1", "T3"]  # T2 is gone
    table_1 = tracker.states[0]
    assert table_1.status is OCCUPIED and table_1.name == "Window table" and table_1.session_count == 1
    assert tracker.states[1].status is AVAILABLE
    assert tracker.settings.enter_seconds == 2
    assert tracker.update(people(AT_TABLE_1), now=6.5) == []  # no spurious events


def test_polygon_scaling() -> None:
    config = TableConfig(
        video_id="cam",
        source="rtsp://localhost:8554/cam1",
        frame_width=1280,
        frame_height=720,
        tables=[TableDef(id="T1", name="Table 1", polygon=[(200, 200), (400, 200), (400, 400), (200, 400)])],
        occupancy=settings(enter_seconds=0),
    )
    scaled = config.tables_for_frame(640, 360)
    assert scaled[0].polygon == [(100, 100), (200, 100), (200, 200), (100, 200)]
    assert config.tables[0].polygon[0] == (200, 200)  # the config itself is unchanged

    tracker = OccupancyTracker(scaled, config.occupancy)
    tracker.update(people(AT_TABLE_1), now=0)  # (150, 150) in the 640x360 frame
    assert status(tracker) is OCCUPIED
