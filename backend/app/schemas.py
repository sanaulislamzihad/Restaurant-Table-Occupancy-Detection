"""Pydantic models shared across the backend: the per-video table config and API payloads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

TableStatusName = Literal["AVAILABLE", "PENDING_OCCUPIED", "OCCUPIED", "PENDING_AVAILABLE"]
SourceLabel = Literal["LIVE", "RECONNECTING", "NO SOURCE"]

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


# ---------------------------------------------------------------- API payloads


class TablesUpdate(BaseModel):
    """Body of PUT /api/config/tables.

    Polygons are in pixels of a frame_width x frame_height frame. If the size
    is left out, the size of the current live frame is used.
    """

    tables: list[TableDef]
    frame_width: int | None = Field(default=None, gt=0)
    frame_height: int | None = Field(default=None, gt=0)


class TableStatusOut(BaseModel):
    """Live status of one table."""

    id: str
    name: str
    status: TableStatusName
    occupied: bool = Field(description="OCCUPIED, or waiting to become AVAILABLE")
    people_count: int
    track_ids: list[int]
    occupied_since: float | None = Field(description="Unix time the current guests arrived")
    current_session_seconds: float
    total_occupied_seconds: float
    session_count: int


class PipelineStatus(BaseModel):
    """What the live pipeline sees right now (sent over the WebSocket)."""

    video_id: str | None
    has_tables: bool
    source_status: SourceLabel
    processing_fps: float
    stream_fps: float
    frame_width: int | None
    frame_height: int | None
    people_count: int
    table_count: int
    available_count: int
    tables: list[TableStatusOut]
    timestamp: float


class EventOut(BaseModel):
    """A stored table status change."""

    id: int
    video_id: str | None
    table_id: str
    table_name: str
    old_status: TableStatusName
    new_status: TableStatusName
    timestamp: float


StreamState = Literal["running", "stopped", "error"]


class HealthOut(BaseModel):
    """GET /api/health."""

    status: Literal["ok"] = "ok"
    pipeline_running: bool
    model: Literal["loading", "ready", "failed"]
    device: str | None
    source: SourceLabel
    processing_fps: float
    stream_fps: float
    video_id: str | None
    mediamtx: Literal["running", "not running"]
    mediamtx_managed: bool = Field(description="started by this backend (vs. already running)")
    ffmpeg: StreamState


class VideoOut(BaseModel):
    """An uploaded video."""

    id: str
    name: str = Field(description="original file name")
    filename: str
    size_bytes: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    codec: str | None
    uploaded_at: float
    has_tables: bool
    table_count: int
    is_streaming: bool
    thumbnail_url: str


class UploadLimitsOut(BaseModel):
    """What POST /api/videos accepts, so the browser can check files before uploading."""

    max_upload_mb: int
    extensions: list[str]


class StreamStartIn(BaseModel):
    """Body of POST /api/stream/start."""

    model_config = {"json_schema_extra": {"examples": [{"video_id": "restaurant"}]}}

    video_id: str = Field(pattern=ID_PATTERN, description="id from GET /api/videos")


class StreamStatusOut(BaseModel):
    """State of the fake CCTV camera (ffmpeg + MediaMTX)."""

    state: StreamState
    video_id: str | None
    video_name: str | None = None
    has_tables: bool = False
    started_at: float | None
    uptime_seconds: float | None
    rtsp_url: str
    error: str | None
    mediamtx: Literal["running", "not running"]
    mediamtx_managed: bool
