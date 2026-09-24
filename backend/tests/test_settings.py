"""Tests for loading settings from the .env template."""

from __future__ import annotations

import pytest

from app.settings import BACKEND_DIR, Settings

ENV_TEMPLATE = BACKEND_DIR / ".env.example"


def test_env_template_loads_and_paths_resolve_from_backend_dir() -> None:
    settings = Settings(_env_file=ENV_TEMPLATE)  # type: ignore[call-arg]
    assert settings.videos_dir == (BACKEND_DIR / "../fake_camera/videos").resolve()
    assert settings.configs_dir == (BACKEND_DIR / "configs").resolve()
    assert settings.fake_camera_rtsp_url.startswith("rtsp://")
    assert all(origin.startswith("http") for origin in settings.cors_origin_list)


def test_environment_variables_override_env_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "9123")
    monkeypatch.setenv("LOOP_VIDEO_FILES", "false")
    settings = Settings(_env_file=ENV_TEMPLATE)  # type: ignore[call-arg]
    assert settings.api_port == 9123
    assert settings.loop_video_files is False
