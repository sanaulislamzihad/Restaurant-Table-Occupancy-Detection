"""Occupancy statistics from the stored table status changes.

Sessions are rebuilt from the events the same way the live tracker counts
them: a session starts when the guests arrived (the PENDING_OCCUPIED event
that led to OCCUPIED) and ends when they left (the PENDING_AVAILABLE event
that led to AVAILABLE). Occupancy starts from AVAILABLE at every run, and the
end of a run closes a session that was still open, so time nobody watched is
never counted. Pure logic, no database or web code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Interval = tuple[float, float]

# Chart bucket sizes in seconds; the smallest one giving at most MAX_BUCKETS points is used.
BUCKET_CHOICES = (10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)
MAX_BUCKETS = 60


@dataclass(frozen=True)
class Session:
    """One stretch of time a table was occupied."""

    table_id: str
    table_name: str
    start: float
    end: float


def overlap(a: Interval, b: Interval) -> float:
    """Seconds shared by two intervals."""
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def merge(intervals: list[Interval]) -> list[Interval]:
    """Sorted, non-overlapping union of the intervals."""
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def choose_bucket(span_seconds: float) -> int:
    """The smallest bucket size that splits the span into at most MAX_BUCKETS points."""
    for bucket in BUCKET_CHOICES:
        if span_seconds / bucket <= MAX_BUCKETS:
            return bucket
    return BUCKET_CHOICES[-1]


def rebuild_sessions(events: list[dict], runs: list[Interval]) -> list[Session]:
    """Occupied sessions from events (dicts with table_id, table_name, new_status, timestamp).

    Only events inside a run count. ``runs`` are (start, end) pairs.
    """
    events = sorted(events, key=lambda event: event["timestamp"])
    sessions: list[Session] = []
    for run_start, run_end in runs:
        # Per table: when the guests arrived, when the session started, when they left.
        open_tables: dict[str, dict] = {}
        for event in events:
            at = event["timestamp"]
            if not run_start <= at <= run_end:
                continue
            table = open_tables.setdefault(event["table_id"], {"arrived": None, "start": None, "left": None})
            table["name"] = event["table_name"]
            status = event["new_status"]
            if status == "PENDING_OCCUPIED":
                table["arrived"] = at
            elif status == "OCCUPIED":
                if table["start"] is None:
                    table["start"] = table["arrived"] if table["arrived"] is not None else at
                table["left"] = None  # came back: same session
            elif status == "PENDING_AVAILABLE":
                table["left"] = at
            elif status == "AVAILABLE":
                if table["start"] is not None:
                    end = table["left"] if table["left"] is not None else at
                    sessions.append(Session(event["table_id"], table["name"], table["start"], end))
                table.update(arrived=None, start=None, left=None)
        for table_id, table in open_tables.items():  # still occupied when the run ended
            if table["start"] is not None:
                end = table["left"] if table["left"] is not None else run_end
                sessions.append(Session(table_id, table["name"], table["start"], end))
    return sorted(sessions, key=lambda session: (session.start, session.table_id))


def compute_analytics(
    events: list[dict],
    runs: list[Interval],
    tables: list[tuple[str, str]],
    since: float,
    until: float,
    bucket_seconds: int | None = None,
) -> dict:
    """Per-table totals and occupancy over time between since and until.

    ``tables`` are the (id, name) pairs of the current config, in order.
    Tables that only appear in the events (since removed) are added after them.
    The result matches schemas.AnalyticsOut.
    """
    monitored = merge([(max(start, since), min(end, until)) for start, end in runs])
    monitored_seconds = sum(end - start for start, end in monitored)

    # Sessions cut to the time range.
    sessions = [
        Session(s.table_id, s.table_name, max(s.start, since), min(s.end, until))
        for s in rebuild_sessions(events, runs)
        if overlap((s.start, s.end), (since, until)) > 0
    ]
    names = dict(tables)
    config_ids = set(names)
    for session in sessions:  # the newest name wins for removed tables
        if session.table_id not in config_ids:
            names[session.table_id] = session.table_name

    table_rows = []
    for table_id, name in names.items():
        durations = [s.end - s.start for s in sessions if s.table_id == table_id]
        occupied = sum(durations)
        table_rows.append({
            "id": table_id,
            "name": name,
            "occupied_seconds": round(occupied, 1),
            "session_count": len(durations),
            "average_session_seconds": round(occupied / len(durations), 1) if durations else None,
            "longest_session_seconds": round(max(durations), 1) if durations else None,
            "occupancy_rate": round(min(1.0, occupied / monitored_seconds), 4) if monitored_seconds else None,
        })

    table_count = len(table_rows)
    total_occupied = sum(row["occupied_seconds"] for row in table_rows)
    first = monitored[0][0] if monitored else since
    bucket = bucket_seconds or choose_bucket(until - first)
    timeline = []
    if monitored:
        start = math.floor(first / bucket) * bucket
        while start < until:
            window = (start, start + bucket)
            watched = sum(overlap(window, interval) for interval in monitored)
            occupied = min(sum(overlap(window, (s.start, s.end)) for s in sessions), watched * table_count)
            timeline.append({
                "start": start,
                "end": start + bucket,
                "monitored_seconds": round(watched, 1),
                "occupancy_rate": round(occupied / (watched * table_count), 4) if watched and table_count else None,
                "occupied_tables": round(occupied / watched, 2) if watched else None,
            })
            start += bucket

    return {
        "since": since,
        "until": until,
        "bucket_seconds": bucket,
        "monitored_seconds": round(monitored_seconds, 1),
        "table_count": table_count,
        "session_count": len(sessions),
        "average_occupancy": (
            round(min(1.0, total_occupied / (monitored_seconds * table_count)), 4)
            if monitored_seconds and table_count else None
        ),
        "tables": table_rows,
        "timeline": timeline,
    }
