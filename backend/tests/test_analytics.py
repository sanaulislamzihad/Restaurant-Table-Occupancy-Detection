"""Tests for rebuilding sessions from events and the occupancy statistics."""

from __future__ import annotations

import pytest

from app.analytics import Session, choose_bucket, compute_analytics, merge, rebuild_sessions

TABLES = [("T1", "Table 1"), ("T2", "Table 2")]


def event(table_id: str, new_status: str, at: float, name: str | None = None) -> dict:
    return {"table_id": table_id, "table_name": name or f"Table {table_id[1:]}", "new_status": new_status,
            "timestamp": at}


# One run from 0 to 1000 s.
EVENTS = [
    event("T1", "PENDING_OCCUPIED", 100),  # guests arrive
    event("T1", "OCCUPIED", 105),
    event("T1", "PENDING_AVAILABLE", 200),  # someone stands up...
    event("T1", "OCCUPIED", 205),  # ...and sits down again: same session
    event("T1", "PENDING_AVAILABLE", 300),  # they leave
    event("T1", "AVAILABLE", 310),
    event("T1", "PENDING_OCCUPIED", 400),  # a passer-by
    event("T1", "AVAILABLE", 402),
    event("T2", "PENDING_OCCUPIED", 500),
    event("T2", "OCCUPIED", 505),  # still there when the run ends
]


def test_sessions_run_from_arrival_to_leaving() -> None:
    assert rebuild_sessions(EVENTS, [(0, 1000)]) == [
        Session("T1", "Table 1", 100, 300),
        Session("T2", "Table 2", 500, 1000),  # closed by the end of the run
    ]


def test_a_run_ending_while_guests_leave_ends_the_session_when_they_left() -> None:
    events = [event("T1", "PENDING_OCCUPIED", 10), event("T1", "OCCUPIED", 15), event("T1", "PENDING_AVAILABLE", 60)]
    assert rebuild_sessions(events, [(0, 65)]) == [Session("T1", "Table 1", 10, 60)]


def test_every_run_starts_over_and_events_outside_runs_are_ignored() -> None:
    events = [
        event("T1", "PENDING_OCCUPIED", 50),
        event("T1", "OCCUPIED", 55),  # run 1 ends at 100 with the table occupied
        event("T1", "AVAILABLE", 150),  # between runs: not counted
        event("T1", "AVAILABLE", 250),  # in run 2, but no session is open
    ]
    assert rebuild_sessions(events, [(0, 100), (200, 300)]) == [Session("T1", "Table 1", 50, 100)]


def test_totals_per_table_and_overall() -> None:
    result = compute_analytics(EVENTS, [(0, 1000)], TABLES, since=0, until=1000)
    assert result["monitored_seconds"] == 1000 and result["table_count"] == 2
    t1, t2 = result["tables"]
    assert (t1["occupied_seconds"], t1["session_count"], t1["occupancy_rate"]) == (200, 1, 0.2)
    assert (t2["occupied_seconds"], t2["session_count"], t2["longest_session_seconds"]) == (500, 1, 500)
    assert result["session_count"] == 2
    assert result["average_occupancy"] == pytest.approx(0.35)  # 700 of 2 x 1000 table-seconds

    assert result["bucket_seconds"] == 30  # 1000 s in at most 60 points
    timeline = result["timeline"]
    assert timeline[0]["start"] == 0 and timeline[-1]["end"] >= 1000
    occupied = sum(p["occupancy_rate"] * p["monitored_seconds"] * 2 for p in timeline)
    assert occupied == pytest.approx(700, abs=1)
    assert timeline[0]["occupancy_rate"] == 0 and timeline[-1]["occupied_tables"] == 1  # only T2 at the end


def test_time_range_cuts_sessions() -> None:
    result = compute_analytics(EVENTS, [(0, 1000)], TABLES, since=250, until=600, bucket_seconds=50)
    assert result["monitored_seconds"] == 350
    assert [t["occupied_seconds"] for t in result["tables"]] == [50, 100]
    assert result["timeline"][0]["start"] == 250 and len(result["timeline"]) == 7


def test_time_between_runs_is_not_watched() -> None:
    result = compute_analytics(EVENTS, [(0, 300), (600, 1000)], TABLES, since=0, until=1000, bucket_seconds=100)
    assert result["monitored_seconds"] == 700
    gap = result["timeline"][4]  # 400..500
    assert gap["monitored_seconds"] == 0 and gap["occupancy_rate"] is None
    # T2's session was in the gap, so only T1 counts.
    assert [t["occupied_seconds"] for t in result["tables"]] == [200, 0]


def test_removed_tables_are_listed_with_their_last_name() -> None:
    events = [event("T9", "PENDING_OCCUPIED", 10, "Bar"), event("T9", "OCCUPIED", 12, "Bar corner")]
    result = compute_analytics(events, [(0, 100)], TABLES, since=0, until=100)
    assert [(t["id"], t["name"]) for t in result["tables"]] == [("T1", "Table 1"), ("T2", "Table 2"), ("T9", "Bar corner")]


def test_nothing_watched() -> None:
    result = compute_analytics([], [], TABLES, since=0, until=100)
    assert result["monitored_seconds"] == 0 and result["average_occupancy"] is None
    assert result["timeline"] == [] and result["tables"][0]["occupancy_rate"] is None


def test_helpers() -> None:
    assert merge([(5, 10), (0, 3), (2, 6), (20, 20)]) == [(0, 10)]
    assert choose_bucket(60) == 10 and choose_bucket(3600) == 60
    assert choose_bucket(10 * 86400) == 21600  # 40 points of 6 hours
    assert choose_bucket(365 * 86400) == 86400  # the largest step, even if that gives more points
