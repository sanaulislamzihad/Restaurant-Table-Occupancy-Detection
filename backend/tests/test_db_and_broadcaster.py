"""Tests for event storage and the thread-safe WebSocket fan-out."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from app.broadcaster import QUEUE_SIZE, Broadcaster
from app.db import Database
from app.occupancy import OccupancyEvent, TableStatus


def test_events_are_stored_and_listed_newest_first(tmp_path: Path) -> None:
    db = Database(tmp_path / "data" / "app.db")
    events = [
        OccupancyEvent("T1", TableStatus.AVAILABLE, TableStatus.PENDING_OCCUPIED, 100.0),
        OccupancyEvent("T1", TableStatus.PENDING_OCCUPIED, TableStatus.OCCUPIED, 105.0),
    ]
    rows = db.add_events("cam", events, {"T1": "Table 1"})
    db.add_events("other", [OccupancyEvent("T9", TableStatus.AVAILABLE, TableStatus.PENDING_OCCUPIED, 106.0)], {})
    assert [row["id"] for row in rows] == [1, 2]

    recent = db.recent_events(limit=10)
    assert [row["timestamp"] for row in recent] == [106.0, 105.0, 100.0]
    assert recent[0]["table_name"] == "T9"  # falls back to the table ID
    cam_only = db.recent_events(limit=1, video_id="cam")
    assert len(cam_only) == 1 and cam_only[0]["new_status"] == "OCCUPIED"
    db.close()


def test_publish_from_another_thread_reaches_subscribers() -> None:
    async def scenario() -> list[dict]:
        broadcaster = Broadcaster()
        queue = broadcaster.subscribe()
        thread = threading.Thread(target=lambda: [broadcaster.publish({"n": i}) for i in range(3)])
        thread.start()
        thread.join()
        received = [await asyncio.wait_for(queue.get(), timeout=1) for _ in range(3)]
        broadcaster.unsubscribe(queue)
        assert broadcaster.subscriber_count == 0
        return received

    assert asyncio.run(scenario()) == [{"n": 0}, {"n": 1}, {"n": 2}]


def test_slow_subscriber_keeps_only_the_newest_messages() -> None:
    async def scenario() -> tuple[int, dict]:
        broadcaster = Broadcaster()
        queue = broadcaster.subscribe()
        for i in range(QUEUE_SIZE + 10):
            broadcaster.publish({"n": i})
        await asyncio.sleep(0.05)  # let the scheduled puts run
        return queue.qsize(), queue.get_nowait()

    size, oldest = asyncio.run(scenario())
    assert size == QUEUE_SIZE
    assert oldest == {"n": 10}  # the 10 oldest were dropped


def test_runs_left_open_by_a_crash_end_at_their_last_heartbeat(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    finished = db.start_run("a", 100)
    db.end_run(finished, 160)
    crashed = db.start_run("a", 200)
    db.touch_run(crashed, 230)
    assert db.runs_between("a", 0, 1000, now=500) == [(100, 160), (200, 500)]  # still open: ends "now"
    assert db.close_unfinished_runs() == 1
    assert db.runs_between("a", 0, 1000, now=500) == [(100, 160), (200, 230)]
    assert db.runs_between("a", 170, 190, now=500) == []
    videos = db.monitored_videos(now=500)
    assert videos[0]["video_id"] == "a" and videos[0]["monitored_seconds"] == 90
    db.close()
