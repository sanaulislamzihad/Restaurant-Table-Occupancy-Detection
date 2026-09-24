"""Pydantic models shared across the backend: the per-video table config."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ReferencePoint = Literal["bottom_center", "center"]
# Video and table IDs end up in file names and URLs, so keep them simple.
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
Point = tuple[float, float]


def scale_polygon(polygon: list[Point], from_size: tuple[int, int], to_size: tuple[int, int]) -> list[Point]:
    """Scale polygon points from one frame size (width, height) to another."""
    scale_x = to_size[0] / from_size[0]
    scale_y = to_size[1] / from_size[1]
    return [(x * scale_x, y * scale_y) for x, y in polygon]


class TableDef(BaseModel):
    """One table: an ID, a display name and its outline in frame pixels."""

    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=64)
    polygon: list[Point] = Field(min_length=3)


class OccupancySettings(BaseModel):
    """How a table decides between AVAILABLE and OCCUPIED."""

    confidence_threshold: float = Field(ge=0, le=1)
    enter_seconds: float = Field(ge=0)
    leave_seconds: float = Field(ge=0)
    reference_point: ReferencePoint = "bottom_center"
    # Detections flicker for a frame or two even when someone sits still. A gap
    # up to this long still counts as "someone is at the table".
    presence_hold_seconds: float = Field(default=1.0, ge=0)


class TableConfig(BaseModel):
    """Table layout of one video or camera, stored as configs/<video_id>.json.

    Polygons are in the pixel coordinates of a frame_width x frame_height frame
    and are scaled when the live frames have another size.
    """

    video_id: str = Field(pattern=ID_PATTERN)
    source: str = Field(min_length=1)
    frame_width: int = Field(gt=0)
    frame_height: int = Field(gt=0)
    tables: list[TableDef] = Field(default_factory=list)
    occupancy: OccupancySettings

    @field_validator("tables")
    @classmethod
    def _table_ids_are_unique(cls, tables: list[TableDef]) -> list[TableDef]:
        ids = [table.id for table in tables]
        if len(ids) != len(set(ids)):
            raise ValueError("table ids must be unique")
        return tables

    def tables_for_frame(self, width: int, height: int) -> list[TableDef]:
        """The tables with their polygons scaled to a width x height frame."""
        from_size, to_size = (self.frame_width, self.frame_height), (width, height)
        return [
            table.model_copy(update={"polygon": scale_polygon(table.polygon, from_size, to_size)})
            for table in self.tables
        ]
