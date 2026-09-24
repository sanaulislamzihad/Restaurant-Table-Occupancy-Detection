"""Tests for logging to the rotating log file."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger

from app.logging_setup import setup_logging
from conftest import make_settings


def test_our_logs_and_uvicorn_logs_go_to_the_file(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, log_dir=tmp_path / "logs")
    try:
        log_file = setup_logging(settings)
        logger.info("pipeline says hello")
        logging.getLogger("uvicorn.error").warning("server says hello")
        logger.debug("too detailed for LOG_LEVEL=INFO")
        logger.complete()  # wait for the queued messages
        text = log_file.read_text(encoding="utf-8")
    finally:
        logger.remove()  # closes the file
        logger.add(sys.stderr)
        logging.basicConfig(handlers=[], force=True)
    assert log_file == tmp_path / "logs" / "backend.log"
    assert "pipeline says hello" in text and "server says hello" in text
    assert "too detailed" not in text
