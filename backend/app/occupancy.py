"""Table occupancy: which tables have people at them, with enter/leave delays.

Pure logic with no drawing and no web code, so it can be tested with fake
detections and explicit timestamps.

Each table runs this state machine:

    AVAILABLE -> PENDING_OCCUPIED -> OCCUPIED -> PENDING_AVAILABLE -> AVAILABLE

* It becomes OCCUPIED only after someone has been at it continuously for
  ``enter_seconds``. If they leave before that (a passer-by), it goes straight
  back to AVAILABLE.
* It becomes AVAILABLE only after nobody has been at it continuously for
  ``leave_seconds``. If someone comes back before that, it stays OCCUPIED.

Times are wall-clock seconds, so the behaviour does not depend on the frame
rate. A person is "at" a table when the chosen anchor point of their box
(bottom centre or centre) is inside the table's polygon.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import supervision as sv

from app.schemas import OccupancySettings, TableDef

_ANCHORS = {"bottom_center": sv.Position.BOTTOM_CENTER, "center": sv.Position.CENTER}


class TableStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    PENDING_OCCUPIED = "PENDING_OCCUPIED"
    OCCUPIED = "OCCUPIED"
    PENDING_AVAILABLE = "PENDING_AVAILABLE"

    @property
    def counts_as_occupied(self) -> bool:
        """A pending table keeps its previous status until the delay has passed."""
        return self in (TableStatus.OCCUPIED, TableStatus.PENDING_AVAILABLE)


def anchor_position(reference_point: str) -> sv.Position:
    """The supervision anchor for a config's reference_point."""
    return _ANCHORS[reference_point]


@dataclass(frozen=True)
class OccupancyEvent:
    """A table changed status."""

    table_id: str
    old_status: TableStatus
    new_status: TableStatus
    timestamp: float  # Unix time in seconds


@dataclass
class TableState:
    """Current status and statistics of one table."""

    table_id: str
    name: str
    status: TableStatus = TableStatus.AVAILABLE
    people_count: int = 0
    track_ids: list[int] = field(default_factory=list)
    occupied_since: float | None = None  # when the people of the current session arrived
    total_occupied_seconds: float = 0.0  # finished sessions; see occupied_seconds()
    session_count: int = 0
    # Timers of the state machine.
    last_seen: float | None = None  # last time anyone was detected at the table
    presence_started: float | None = None  # start of the current run of presence
    absence_started: float | None = None  # start of the current run of absence

    def occupied_seconds(self, now: float) -> float:
        """Total occupied time, including the session in progress."""
        if self.occupied_since is None:
            return self.total_occupied_seconds
        # While waiting to become AVAILABLE, the session ended when people left.
        leaving = self.status is TableStatus.PENDING_AVAILABLE and self.absence_started is not None
        session_end = self.absence_started if leaving else now
        return self.total_occupied_seconds + max(0.0, session_end - self.occupied_since)  # type: ignore[operator]


class OccupancyTracker:
    """Runs the occupancy state machine for a set of tables."""

    def __init__(self, tables: list[TableDef], settings: OccupancySettings) -> None:
        self.tables = tables
        self.settings = settings
        anchor = anchor_position(settings.reference_point)
        self._zones = {
            table.id: sv.PolygonZone(
                polygon=np.rint(np.array(table.polygon)).astype(int),
                triggering_anchors=(anchor,),
            )
            for table in tables
        }
        self._states = {table.id: TableState(table_id=table.id, name=table.name) for table in tables}

    @property
    def states(self) -> list[TableState]:
        """State of every table, in config order."""
        return [self._states[table.id] for table in self.tables]

    def update(self, detections: sv.Detections, now: float | None = None) -> list[OccupancyEvent]:
        """Feed the people found in one frame; returns the status changes it caused.

        ``now`` is the frame's wall-clock time (defaults to the current time).
        """
        now = time.time() if now is None else now
        if detections.confidence is not None and len(detections):
            detections = detections[detections.confidence >= self.settings.confidence_threshold]

        events: list[OccupancyEvent] = []
        for table in self.tables:
            state = self._states[table.id]
            inside = self._zones[table.id].trigger(detections)
            state.people_count = int(inside.sum())
            track_ids = detections.tracker_id[inside] if detections.tracker_id is not None else []
            state.track_ids = sorted(int(track_id) for track_id in track_ids if track_id >= 0)
            events.extend(self._advance(state, bool(inside.any()), now))
        return events

    def _advance(self, state: TableState, detected: bool, now: float) -> list[OccupancyEvent]:
        """Update one table's timers and status."""
        if detected:
            state.last_seen = now
        present = state.last_seen is not None and now - state.last_seen <= self.settings.presence_hold_seconds

        if present:
            if state.presence_started is None:
                state.presence_started = now
            state.absence_started = None
        else:
            if state.absence_started is None:
                # Absence began when the table was last seen occupied.
                state.absence_started = state.last_seen if state.last_seen is not None else now
            state.presence_started = None

        events: list[OccupancyEvent] = []

        def move_to(new_status: TableStatus) -> None:
            events.append(OccupancyEvent(state.table_id, state.status, new_status, now))
            state.status = new_status

        # Loop so a zero delay can pass through a pending state in one update.
        for _ in range(4):
            status = state.status
            if status is TableStatus.AVAILABLE and present:
                move_to(TableStatus.PENDING_OCCUPIED)
            elif status is TableStatus.PENDING_OCCUPIED:
                if not present:
                    move_to(TableStatus.AVAILABLE)  # a passer-by
                elif now - state.presence_started >= self.settings.enter_seconds:  # type: ignore[operator]
                    state.occupied_since = state.presence_started
                    state.session_count += 1
                    move_to(TableStatus.OCCUPIED)
            elif status is TableStatus.OCCUPIED and not present:
                move_to(TableStatus.PENDING_AVAILABLE)
            elif status is TableStatus.PENDING_AVAILABLE:
                if present:
                    move_to(TableStatus.OCCUPIED)  # came back: same session
                elif now - state.absence_started >= self.settings.leave_seconds:  # type: ignore[operator]
                    state.total_occupied_seconds += max(0.0, state.absence_started - state.occupied_since)  # type: ignore[operator]
                    state.occupied_since = None
                    move_to(TableStatus.AVAILABLE)
            if state.status is status:
                break
        return events
