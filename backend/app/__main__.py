"""Run the backend: ``python -m app`` from the backend folder.

Host and port come from API_HOST and API_PORT in backend/.env.

    python -m app                    # watch DEFAULT_VIDEO_ID from backend/.env
    python -m app --video restaurant # watch the "restaurant" table config
"""

from __future__ import annotations

import argparse
import os
import sys
from types import FrameType

import uvicorn
from loguru import logger


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="Run the table occupancy backend.")
    parser.add_argument("--video", help="video ID / table config to watch at startup (overrides DEFAULT_VIDEO_ID)")
    args = parser.parse_args()
    if args.video:
        os.environ["DEFAULT_VIDEO_ID"] = args.video  # read by the settings below

    from app.logging_setup import setup_logging
    from app.settings import get_settings

    settings = get_settings()
    log_file = setup_logging(settings)
    logger.info("Logging to {}", log_file)

    from app.main import app, shutdown_requested

    class Server(uvicorn.Server):
        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            shutdown_requested.set()  # ends open MJPEG streams and WebSockets so shutdown is quick
            super().handle_exit(sig, frame)

    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        log_config=None,  # uvicorn's logs go through loguru (see logging_setup.py)
        timeout_graceful_shutdown=5,
    )
    try:
        Server(config).run()
    except KeyboardInterrupt:  # a second Ctrl+C
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
