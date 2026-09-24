"""Hand messages from the pipeline thread to WebSocket clients.

Each client gets an asyncio queue on its own event loop; ``publish`` can be
called from any thread. A client that falls behind loses its oldest messages
instead of slowing everyone down.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

QUEUE_SIZE = 100


def _put_dropping_oldest(queue: asyncio.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(message)


class Broadcaster:
    """Thread-safe fan-out of JSON-ready messages to subscribers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop] = {}

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a client; call from the client's event loop."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._subscribers[queue] = asyncio.get_running_loop()
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, message: dict[str, Any]) -> None:
        """Send a message to every subscriber (safe to call from any thread)."""
        with self._lock:
            subscribers = list(self._subscribers.items())
        for queue, loop in subscribers:
            try:
                loop.call_soon_threadsafe(_put_dropping_oldest, queue, message)
            except RuntimeError:  # that client's loop has closed
                self.unsubscribe(queue)
