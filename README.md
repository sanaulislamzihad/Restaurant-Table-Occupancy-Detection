# Restaurant Table Occupancy Detection

Detects whether each table in a restaurant is **AVAILABLE** or **OCCUPIED** from a CCTV feed and shows the result on a live web dashboard.

There is no live camera yet, so uploaded CCTV footage is played as a looping **fake RTSP camera** (MediaMTX + ffmpeg). The detection pipeline reads that RTSP stream exactly like a real camera, so moving to a real CCTV camera later is a config change, not a code change.

> **Status:** work in progress. The fake RTSP camera, the frame reader and person detection with tracking work; table occupancy, the API and the dashboard are being built.

## How it works

```text
Browser upload → backend saves video → ffmpeg loops it in real time
                                              ↓
                                   MediaMTX → rtsp://localhost:8554/cam1
                                              ↓
                                   Frame reader (keeps only the latest frame)
                                              ↓
                                   YOLO person detection + ByteTrack
                                              ↓
                                   Table zones (JSON config per video)
                                              ↓
                                   Occupancy state machine
                                    ↙                     ↘
                      Annotated video (MJPEG)     Status + events (WebSocket / REST)
                                    ↘                     ↙
                                         React dashboard
```

## Tech stack

| Part | Tool |
|---|---|
| Fake camera | MediaMTX (RTSP server) + ffmpeg, managed by the backend |
| Detection + tracking | Ultralytics YOLO11n (person class) + ByteTrack |
| Zones and drawing | supervision + OpenCV |
| Backend | FastAPI + Uvicorn, SQLite |
| Live video / live status | MJPEG stream / WebSocket |
| Frontend | React + Vite + Tailwind CSS |

## Requirements

| Tool | Version | Check |
|---|---|---|
| Python | 3.10 or newer (tested with 3.14.2) | `python --version` |
| Node.js | 18 or newer | `node --version` |
| git | any recent version | `git --version` |
| ffmpeg + ffprobe | any recent build with libx264 | `ffmpeg -version` |
| MediaMTX | downloaded by a script (setup step 4) | |

A CPU is enough. If an NVIDIA GPU with CUDA is available, it is used automatically.

## Setup

### 1. Get the code

```bash
git clone https://github.com/sanaulislamzihad/Restaurant-Table-Occupancy-Detection.git
cd Restaurant-Table-Occupancy-Detection
```

### 2. Install ffmpeg

- **Windows:** `winget install Gyan.FFmpeg`, then restart the terminal (or VS Code) so it picks up the new PATH.
- **Linux (Debian/Ubuntu):** `sudo apt install ffmpeg`

Check with `ffmpeg -version` and `ffprobe -version`.

### 3. Python environment

Windows (PowerShell):

```powershell
py -3.14 -m venv .venv            # or: python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

If PowerShell refuses to run `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or call `.\.venv\Scripts\python.exe` directly instead of `python`.

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

**NVIDIA GPU (optional):** on Windows the default PyTorch wheel is CPU-only. Before the last command, install the CUDA build of the same `torch` and `torchvision` versions listed in `backend/requirements.txt`, using the command for your CUDA version from <https://pytorch.org/get-started/locally/>.

### 4. Download MediaMTX

```bash
python fake_camera/download_mediamtx.py
```

This downloads the latest MediaMTX release for your OS into `fake_camera/bin/`, verifies its SHA-256 checksum and runs `mediamtx --version`. Add `--tag v1.21.1` to get a specific release.

### 5. Configuration

```bash
copy backend\.env.example backend\.env      # Windows
cp backend/.env.example backend/.env        # Linux / macOS
```

All paths, ports, URLs and tuning values live in `backend/.env` or in the per-video table configs in `backend/configs/`. Nothing is hardcoded.

## Running

### Fake CCTV camera (manual)

1. Copy a restaurant video (`.mp4`, `.avi`, `.mov` or `.mkv`) into `fake_camera/videos/`.
2. Start the camera from the project folder:

   ```powershell
   fake_camera\start_camera.bat my_video.mp4      # Windows
   ./fake_camera/start_camera.sh my_video.mp4     # Linux / macOS
   ```

   Without a file name it lists the videos in `fake_camera/videos/`; a path to a video anywhere else works too. The script starts MediaMTX (unless one is already running) and streams the video to `rtsp://localhost:8554/cam1` in real time, looping forever. It prints the exact ffmpeg command it runs. `Ctrl+C` stops everything.
3. Open the stream in a player:
   - **VLC:** Media → Open Network Stream → `rtsp://localhost:8554/cam1`
   - **ffplay** (installed with ffmpeg): `ffplay -rtsp_transport tcp rtsp://localhost:8554/cam1`

The stream URL and port come from `FAKE_CAMERA_RTSP_URL` in `backend/.env`. Only RTSP over TCP is enabled in `fake_camera/mediamtx.yml`. If Windows asks for firewall access for MediaMTX, **Cancel** keeps the camera reachable from this computer only.

### Preview a video source

`FrameSource` (`backend/app/sources.py`) reads frames from a video file, an RTSP/HTTP stream or a webcam with the same code. It keeps only the newest frame, reconnects to streams and webcams by itself, and plays files at their own frame rate on a loop. To see it working (with the virtual environment active):

```bash
python backend/scripts/preview_source.py                                  # FAKE_CAMERA_RTSP_URL (start the fake camera first)
python backend/scripts/preview_source.py fake_camera/videos/my_video.mp4  # a video file
python backend/scripts/preview_source.py 0                                # webcam 0
```

The window shows the source status (LIVE, RECONNECTING, ...), its frame rate and the frame size. While previewing the RTSP URL, stop the fake camera: the status switches to RECONNECTING, and the picture comes back by itself when the camera starts again. Press `q` or `Esc` to quit.

### Preview person detection and tracking

`PersonDetector` (`backend/app/detector.py`) runs Ultralytics YOLO (person class only) with ByteTrack, so every person gets an ID that stays the same across frames. The weights are downloaded into `backend/models/` on first use. It runs on an NVIDIA GPU automatically when CUDA is available, otherwise on the CPU.

```bash
python backend/scripts/preview_detection.py                                  # the fake camera stream
python backend/scripts/preview_detection.py fake_camera/videos/my_video.mp4  # a video file
python backend/scripts/preview_detection.py --conf 0.5 --every 2             # stricter, detect every 2nd frame
```

Each person is drawn with a box, `#ID confidence` and a short trail; a seated person should keep the same ID. The model, image size, device and confidence threshold are set in `backend/.env`.

Model choice, measured on a 640x360 overhead restaurant CCTV clip (about 25 people in view, detection on every 4th frame, Intel i5-1235U CPU, `YOLO_IMG_SIZE=640`):

| Model | Confidence | Tracked people per frame | Time per detection |
|---|---|---|---|
| `yolo11n.pt` | 0.40 | 3.6 | ~45-65 ms |
| `yolo26s.pt` | 0.25 | 13.4 | ~170 ms |
| **`yolo26s.pt` (default)** | **0.15** | **14.8** | **~170 ms** |
| `yolo26s.pt` at `YOLO_IMG_SIZE=960` | 0.15 | 15.5 | ~235 ms |

The default detects about four times more people than `yolo11n`; the people it still misses are mostly hidden behind the counter or tables. Occupancy only needs a few detections per second, so ~6 per second on a CPU is enough. Set `YOLO_MODEL=yolo11n.pt` for a much faster but less accurate model.

More run steps will be added as each part is finished. The goal is a single `run_all.bat` that starts the backend (which starts MediaMTX by itself) and the dashboard.

## Tests

```bash
python -m pytest backend
```

## Project structure

```text
.
├── fake_camera/
│   ├── download_mediamtx.py   # fetches the MediaMTX binary into bin/
│   ├── mediamtx.yml           # MediaMTX config (RTSP over TCP only)
│   ├── start_camera.py        # loops a video as a live RTSP stream
│   ├── start_camera.bat       # Windows launcher
│   ├── start_camera.sh        # Linux / macOS launcher
│   ├── bin/                   # MediaMTX executable (gitignored)
│   └── videos/                # footage (gitignored)
├── backend/
│   ├── app/                   # FastAPI application
│   │   ├── settings.py        # typed settings from backend/.env
│   │   ├── sources.py         # FrameSource: file / stream / webcam reader
│   │   └── detector.py        # PersonDetector: YOLO + ByteTrack
│   ├── scripts/               # developer tools (source / detection preview)
│   ├── models/                # YOLO weights, downloaded on first use (gitignored)
│   ├── configs/               # <video_id>.json table configs
│   ├── tests/                 # pytest tests
│   ├── requirements.txt       # pinned Python dependencies
│   └── .env.example           # configuration template
├── frontend/                  # React dashboard (not started yet)
└── README.md
```
