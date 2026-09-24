"""Application settings.

Every value comes from the environment or from the .env files in backend/:
.env.example provides the defaults and .env (if present) overrides them, so no
path, URL or port is hardcoded in the code. Relative paths are resolved from
the backend/ folder, whatever the current working directory is.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed view of backend/.env (see .env.example for what each value means)."""

    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env.example", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API server
    api_host: str
    api_port: int = Field(ge=1, le=65535)
    cors_origins: str
    log_level: str
    log_dir: Path

    # External tools
    ffmpeg_path: str
    ffprobe_path: str
    mediamtx_path: str
    mediamtx_config: Path
    manage_mediamtx: bool

    # Fake CCTV camera
    fake_camera_rtsp_url: str

    # Storage
    videos_dir: Path
    configs_dir: Path
    data_dir: Path
    max_upload_mb: int = Field(gt=0)

    # Detection
    yolo_model: str
    models_dir: Path
    device: str
    yolo_img_size: int = Field(ge=32)
    confidence_threshold: float = Field(ge=0, le=1)
    detect_every_n_frames: int = Field(ge=1)

    # Live pipeline
    default_video_id: str
    stream_jpeg_quality: int = Field(ge=1, le=100)

    # Occupancy defaults for new table configs
    enter_seconds: float = Field(ge=0)
    leave_seconds: float = Field(ge=0)
    reference_point: Literal["bottom_center", "center"]

    # Video source
    source_reconnect_seconds: float = Field(gt=0)
    source_open_timeout_seconds: float = Field(gt=0)
    source_read_timeout_seconds: float = Field(gt=0)
    loop_video_files: bool

    @field_validator("log_dir", "mediamtx_config", "videos_dir", "configs_dir", "data_dir", "models_dir")
    @classmethod
    def _resolve_from_backend_dir(cls, value: Path) -> Path:
        return (BACKEND_DIR / value).resolve()

    @property
    def yolo_model_path(self) -> Path:
        """YOLO_MODEL as a path: a bare file name lives in MODELS_DIR."""
        model = Path(self.yolo_model)
        if model.parent == Path("."):
            return self.models_dir / model
        return (BACKEND_DIR / model).resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS_ORIGINS split into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Load the settings once and reuse them."""
    return Settings()  # type: ignore[call-arg]  # values come from the .env files
