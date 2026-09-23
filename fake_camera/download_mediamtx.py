"""Download the MediaMTX RTSP server into ``fake_camera/bin/``.

Picks the release asset that matches this machine (Windows / Linux / macOS,
amd64 / arm64 / armv7 / armv6), verifies its SHA-256 checksum against the
release's ``checksums.sha256`` file, and extracts only the executable and its
license. Uses the standard library only, so it works before the virtual
environment exists.

Usage:
    python fake_camera/download_mediamtx.py                 # latest release
    python fake_camera/download_mediamtx.py --tag v1.21.1   # a specific release
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

RELEASES_API = "https://api.github.com/repos/bluenviron/mediamtx/releases"
BIN_DIR = Path(__file__).resolve().parent / "bin"

# platform.system() / platform.machine() values -> names used in asset file names.
_OS_NAMES = {"windows": "windows", "linux": "linux", "darwin": "darwin"}
_ARCH_NAMES = {
    "amd64": "amd64",
    "x86_64": "amd64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "armv7l": "armv7",
    "armv6l": "armv6",
}


def http_get(url: str, accept: str | None = None) -> bytes:
    """Return the body of a GET request (raises on HTTP errors)."""
    headers = {"User-Agent": "table-occupancy-setup"}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def asset_suffix() -> str:
    """Return the asset suffix for this OS/CPU, e.g. ``_windows_amd64.zip``."""
    os_name = _OS_NAMES.get(platform.system().lower())
    arch = _ARCH_NAMES.get(platform.machine().lower())
    if os_name is None or arch is None:
        sys.exit(f"Unsupported platform: {platform.system()} / {platform.machine()}")
    extension = "zip" if os_name == "windows" else "tar.gz"
    return f"_{os_name}_{arch}.{extension}"


def verify_checksum(archive: bytes, asset_name: str, checksums: str) -> None:
    """Exit if the archive does not match its line in ``checksums.sha256``."""
    expected = {}
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2:
            digest, file_name = parts
            expected[file_name.lstrip("*")] = digest.lower()
    if expected.get(asset_name) != hashlib.sha256(archive).hexdigest():
        sys.exit(f"Checksum mismatch for {asset_name}; aborting.")


def extract(archive: bytes, asset_name: str, wanted: set[str]) -> None:
    """Extract the files named in ``wanted`` (matched by base name) into BIN_DIR."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    found: set[str] = set()
    if asset_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            for member in zf.namelist():
                name = PurePosixPath(member).name
                if name in wanted:
                    (BIN_DIR / name).write_bytes(zf.read(member))
                    found.add(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
            for member in tf.getmembers():
                name = PurePosixPath(member.name).name
                file_obj = tf.extractfile(member) if member.isfile() else None
                if name in wanted and file_obj is not None:
                    (BIN_DIR / name).write_bytes(file_obj.read())
                    found.add(name)
    missing = wanted - found
    if missing:
        sys.exit(f"{asset_name} does not contain: {', '.join(sorted(missing))}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download MediaMTX into fake_camera/bin/.")
    parser.add_argument("--tag", help="release tag such as v1.21.1 (default: latest)")
    args = parser.parse_args()

    release_url = f"{RELEASES_API}/tags/{args.tag}" if args.tag else f"{RELEASES_API}/latest"
    release = json.loads(http_get(release_url, accept="application/vnd.github+json"))
    assets = {a["name"]: a["browser_download_url"] for a in release["assets"]}

    suffix = asset_suffix()
    asset_name = next((name for name in assets if name.endswith(suffix)), None)
    if asset_name is None:
        sys.exit(f"Release {release['tag_name']} has no asset ending with '{suffix}'.")

    print(f"Downloading {asset_name} ...")
    archive = http_get(assets[asset_name])

    if "checksums.sha256" in assets:
        verify_checksum(archive, asset_name, http_get(assets["checksums.sha256"]).decode())
        print("SHA-256 checksum OK.")
    else:
        print("Warning: release has no checksums.sha256, skipping verification.")

    exe_name = "mediamtx.exe" if asset_name.endswith(".zip") else "mediamtx"
    extract(archive, asset_name, {exe_name, "LICENSE"})

    exe_path = BIN_DIR / exe_name
    exe_path.chmod(exe_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Prove the binary actually runs on this machine.
    result = subprocess.run(
        [str(exe_path), "--version"], capture_output=True, text=True, check=True, timeout=30
    )
    print(f"MediaMTX {result.stdout.strip()} installed at {exe_path}")


if __name__ == "__main__":
    main()
