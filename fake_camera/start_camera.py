r"""Fake CCTV camera: publish a video file as a live, looping RTSP stream.

Starts MediaMTX when nothing is listening on the RTSP port yet, then runs
ffmpeg to push the video in real time, looping forever, so any RTSP client
(VLC, OpenCV, the detection pipeline) sees it as a live camera.

Settings come from backend/.env, or backend/.env.example when .env does not
exist. Environment variables with the same names take precedence, and relative
paths are resolved from the backend/ folder. Uses the standard library only.

Usage (from the project folder):
    fake_camera\start_camera.bat <video>        Windows
    ./fake_camera/start_camera.sh <video>       Linux / macOS

<video> is a file name inside VIDEOS_DIR or a path to a video file. Run it
without arguments to list the videos in VIDEOS_DIR. Stop with Ctrl+C.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
SETTING_KEYS = (
    "FFMPEG_PATH",
    "MEDIAMTX_PATH",
    "MEDIAMTX_CONFIG",
    "MANAGE_MEDIAMTX",
    "FAKE_CAMERA_RTSP_URL",
    "VIDEOS_DIR",
)
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}
MEDIAMTX_START_TIMEOUT_S = 10.0
# ffmpeg gives up if the RTSP server does not answer for this long. Without a
# timeout, a failed connection can hang forever on Windows.
FFMPEG_SOCKET_TIMEOUT_US = 5_000_000


def read_settings() -> dict[str, str]:
    """Read this script's settings from backend/.env; environment variables win."""
    env_file = BACKEND_DIR / ".env"
    if not env_file.is_file():
        env_file = BACKEND_DIR / ".env.example"
    settings: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]  # drop surrounding quotes
        settings[key] = value
    settings.update({key: os.environ[key] for key in SETTING_KEYS if key in os.environ})
    missing = [key for key in SETTING_KEYS if not settings.get(key)]
    if missing:
        sys.exit(f"Missing settings in {env_file}: {', '.join(missing)}")
    return settings


def resolve_path(value: str) -> Path:
    """Resolve a path from the settings; relative paths start at backend/."""
    return (BACKEND_DIR / value).resolve()


def resolve_executable(value: str) -> str:
    """Find an executable given as a command on PATH or as a path (".exe" optional)."""
    if "/" not in value and "\\" not in value:
        found = shutil.which(value)
    else:
        path = resolve_path(value)
        options = [path, path.with_name(path.name + ".exe")] if os.name == "nt" else [path]
        found = next((str(p) for p in options if p.is_file() and os.access(p, os.X_OK)), None)
    if found is None:
        sys.exit(
            f"Executable not found: {value} "
            "(check backend/.env, or restart the terminal after installing it)"
        )
    return found


def list_videos(videos_dir: Path) -> list[str]:
    """Names of the video files in videos_dir."""
    if not videos_dir.is_dir():
        return []
    return sorted(p.name for p in videos_dir.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS)


def find_video(name: str, videos_dir: Path) -> Path:
    """Accept a path (relative to the current folder) or a file name inside videos_dir."""
    for candidate in (Path(name), videos_dir / name):
        if candidate.is_file():
            return candidate.resolve()
    sys.exit(f"Video not found: {name} (looked in the current folder and in {videos_dir})")


def rtsp_host_port(url: str) -> tuple[str, int]:
    """Host and port of an rtsp:// URL (the port defaults to 554)."""
    parts = urlsplit(url)
    if parts.scheme != "rtsp" or not parts.hostname or not parts.path.strip("/"):
        sys.exit(f"FAKE_CAMERA_RTSP_URL must look like rtsp://host:port/path, got: {url}")
    return parts.hostname, parts.port or 554


def is_listening(host: str, port: int) -> bool:
    """True if a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def stop_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate a child process, and kill it if it is still running after 5 s."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def start_mediamtx(settings: dict[str, str], host: str, port: int) -> subprocess.Popen[bytes]:
    """Start MediaMTX with its RTSP listener on the port of the fake camera URL."""
    executable = resolve_executable(settings["MEDIAMTX_PATH"])
    config = resolve_path(settings["MEDIAMTX_CONFIG"])
    if not config.is_file():
        sys.exit(f"MediaMTX config not found: {config}")

    # Overrides rtspAddress in mediamtx.yml, so the port lives in backend/.env only.
    env = {**os.environ, "MTX_RTSPADDRESS": f":{port}"}
    print(f"Starting MediaMTX on port {port} ...", flush=True)
    process = subprocess.Popen([executable, str(config)], cwd=config.parent, env=env)
    deadline = time.monotonic() + MEDIAMTX_START_TIMEOUT_S
    try:
        while not is_listening(host, port):
            if process.poll() is not None:
                sys.exit(f"MediaMTX exited with code {process.returncode}, see its output above.")
            if time.monotonic() > deadline:
                sys.exit(f"MediaMTX did not open port {port} within {MEDIAMTX_START_TIMEOUT_S:.0f} s.")
            time.sleep(0.25)
    except BaseException:  # also covers sys.exit() and Ctrl+C
        stop_process(process)
        raise
    return process


def ffmpeg_command(ffmpeg: str, video: Path, rtsp_url: str) -> list[str]:
    """ffmpeg arguments that publish the video in real time, looping forever."""
    return [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-stats",
        "-re",                                          # real-time speed, like a live camera
        "-stream_loop", "-1",                           # loop the file forever
        "-i", str(video),
        "-map", "0:v:0",                                # first video stream only, no audio
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",     # H.264 needs an even width and height
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:gte(t,n_forced*2)",  # keyframe every 2 s so viewers start fast
        "-f", "rtsp", "-rtsp_transport", "tcp", "-timeout", str(FFMPEG_SOCKET_TIMEOUT_US),
        rtsp_url,
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Loop a video file as a fake live RTSP camera.")
    parser.add_argument("video", nargs="?", help="file name in VIDEOS_DIR, or a path to a video file")
    args = parser.parse_args()

    settings = read_settings()
    videos_dir = resolve_path(settings["VIDEOS_DIR"])
    if args.video is None:
        names = list_videos(videos_dir)
        print(f"Usage: start_camera <video>\n\nVideos in {videos_dir}:")
        print("\n".join(f"  {name}" for name in names) if names else "  (none yet, copy a video here)")
        return 1

    video = find_video(args.video, videos_dir)
    rtsp_url = settings["FAKE_CAMERA_RTSP_URL"]
    host, port = rtsp_host_port(rtsp_url)
    ffmpeg = resolve_executable(settings["FFMPEG_PATH"])

    mediamtx: subprocess.Popen[bytes] | None = None
    if is_listening(host, port):
        print(f"Using the RTSP server already running on {host}:{port}.", flush=True)
    elif settings["MANAGE_MEDIAMTX"].strip().lower() in {"1", "true", "yes", "on"}:
        mediamtx = start_mediamtx(settings, host, port)
    else:
        sys.exit(f"Nothing is listening on {host}:{port} and MANAGE_MEDIAMTX is off. Start MediaMTX first.")

    try:
        command = ffmpeg_command(ffmpeg, video, rtsp_url)
        print(f"Streaming {video.name} to {rtsp_url} on a loop. Press Ctrl+C to stop.", flush=True)
        print(subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command), flush=True)
        ffmpeg_process = subprocess.Popen(command)
        try:
            code = ffmpeg_process.wait()
        except KeyboardInterrupt:  # Ctrl+C reaches ffmpeg too, so it is already stopping
            stop_process(ffmpeg_process)
            print("Stopped.", flush=True)
            return 0
        print(f"ffmpeg exited with code {code}.", flush=True)
        return code
    finally:
        if mediamtx is not None:
            stop_process(mediamtx)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
