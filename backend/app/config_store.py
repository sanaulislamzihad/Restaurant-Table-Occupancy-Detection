"""Load and save each video's table config as <configs_dir>/<video_id>.json."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from app.geometry import outline_problem
from app.schemas import ID_PATTERN, OccupancySettings, TableConfig, TablesUpdate

if TYPE_CHECKING:
    from app.settings import Settings


class ConfigError(ValueError):
    """A table config change was rejected; the message says why."""


def build_table_config(
    video_id: str,
    update: TablesUpdate,
    existing: TableConfig | None,
    source: str,
    settings: Settings,
    live_size: tuple[int, int] | None = None,
) -> TableConfig:
    """A checked config with new table outlines.

    The existing config's source and occupancy settings are kept; a new config
    gets ``source`` and the defaults from backend/.env. The frame size comes
    from the update, else the live frame, else the existing config.
    """
    width = update.frame_width or (live_size[0] if live_size else existing.frame_width if existing else None)
    height = update.frame_height or (live_size[1] if live_size else existing.frame_height if existing else None)
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
        return TableConfig(
            video_id=video_id,
            source=existing.source if existing else source,
            frame_width=width,
            frame_height=height,
            tables=update.tables,
            occupancy=existing.occupancy if existing else default_occupancy(settings),
        )
    except ValidationError as error:
        raise ConfigError(str(error)) from error


def default_occupancy(settings: Settings) -> OccupancySettings:
    """Occupancy settings for a new table config, taken from backend/.env."""
    return OccupancySettings(
        confidence_threshold=settings.confidence_threshold,
        enter_seconds=settings.enter_seconds,
        leave_seconds=settings.leave_seconds,
        reference_point=settings.reference_point,
    )


class ConfigStore:
    """Table configs on disk, one JSON file per video ID."""

    def __init__(self, configs_dir: Path) -> None:
        self.configs_dir = Path(configs_dir)
        self.configs_dir.mkdir(parents=True, exist_ok=True)

    def path(self, video_id: str) -> Path:
        """File of a video's config. Rejects IDs that are not plain names."""
        if not re.fullmatch(ID_PATTERN, video_id):
            raise ValueError(f"Invalid video id: {video_id!r}")
        return self.configs_dir / f"{video_id}.json"

    def exists(self, video_id: str) -> bool:
        return self.path(video_id).is_file()

    def load(self, video_id: str) -> TableConfig | None:
        """The config of a video, or None if it has none yet.

        Raises pydantic.ValidationError if the file is not a valid config.
        """
        path = self.path(video_id)
        if not path.is_file():
            return None
        return TableConfig.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, config: TableConfig) -> Path:
        """Write the config atomically, so readers never see a half-written file."""
        path = self.path(config.video_id)
        temp_path = path.with_name(path.name + ".tmp")
        temp_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temp_path, path)
        return path

    def delete(self, video_id: str) -> bool:
        """Remove a video's config. Returns False if there was none."""
        path = self.path(video_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def list_ids(self) -> list[str]:
        """IDs of all videos that have a config."""
        return sorted(path.stem for path in self.configs_dir.glob("*.json"))
