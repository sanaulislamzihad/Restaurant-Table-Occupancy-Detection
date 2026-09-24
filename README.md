# Restaurant Table Occupancy Detection

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Ultralytics YOLO](https://img.shields.io/badge/Ultralytics-YOLO-111F68)
![Windows | Linux](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey)

Real-time table occupancy for restaurants from a CCTV feed. People are detected and tracked with YOLO, each table is watched through its own outline, and a web dashboard shows which tables are **AVAILABLE** or **OCCUPIED**, live, with analytics over time.

Without a live camera, uploaded footage is replayed as a looping **RTSP camera** (MediaMTX + ffmpeg, managed by the backend). The pipeline reads that stream exactly like a real CCTV feed, so switching to a real camera is a configuration change, not a code change.

![Live Monitor: annotated video and a card per table](docs/screenshots/live-monitor.jpg)

## Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [Usage](#usage)
- [Using a real CCTV camera](#using-a-real-cctv-camera)
- [Configuration](#configuration)
- [Performance](#performance)
- [Reliability](#reliability)
- [API reference](#api-reference)
- [Development](#development)
- [License](#license)

## Features

- **Video library:** upload restaurant footage in the browser (mp4, avi, mov, mkv) and stream any video as a live RTSP camera with one click.
- **Detection and tracking:** Ultralytics YOLO with ByteTrack gives every person an ID that stays the same across frames.
- **Per-table occupancy:** a state machine per table with enter and leave delays, so passers-by and short absences do not flip a table.
- **Live Monitor:** annotated video, a status card per table with an "occupied for" timer, tables available, connection state and an event log.
- **Table editor:** draw and adjust table outlines in the browser, start from outlines suggested by the detector, or copy them from another video. Changes apply to the live view immediately.
- **Analytics:** occupied time, sessions and occupancy over time per table, counted only while the video is actually being watched.
- **Built for real cameras:** reconnects by itself, pauses counting during outages, and never leaves orphan ffmpeg or MediaMTX processes behind.
- **No hardcoded values:** paths, ports, URLs and tuning live in `backend/.env` and per-video JSON configs. Runs on Windows and Linux, on the CPU or an NVIDIA GPU.

## Screenshots

| Videos | Table Setup | Analytics |
|---|---|---|
| ![Videos page](docs/screenshots/videos.jpg) | ![Table Setup page](docs/screenshots/table-setup.jpg) | ![Analytics page](docs/screenshots/analytics.jpg) |
| Upload footage and start or stop the camera. | Draw, adjust and name the table outlines. | Occupancy over time and per table. |

## Architecture

```mermaid
flowchart LR
    V[Uploaded video] --> F["ffmpeg<br/>loops it in real time"]
    F --> M["MediaMTX<br/>RTSP server"]
    M -->|rtsp://localhost:8554/cam1| R["Frame reader<br/>newest frame only"]
    C[Real CCTV camera] -.->|change source in the config| R
    R --> D["YOLO + ByteTrack<br/>people with track IDs"]
    D --> O["Occupancy state machine<br/>one per table outline"]
    O --> DB[("SQLite<br/>events and runs")]
    O --> WS[WebSocket: status and events]
    R --> MJ[MJPEG: annotated video]
    DB --> AN[Analytics API]
    WS --> UI[React dashboard]
    MJ --> UI
    AN --> UI
```

- **Pipeline** (`backend/app/pipeline.py`): a frame thread reads every frame, draws the latest results on it and serves it as MJPEG, so the video stays smooth at its own frame rate. A detection thread takes the newest frame and runs detection, tracking and occupancy as fast as the hardware allows. The pipeline only receives frames; it does not know whether they come from a file, an RTSP stream or a webcam.
- **Occupancy** (`backend/app/occupancy.py`): a person is at a table when the bottom centre of their box (or its centre) is inside the table's outline. A table becomes OCCUPIED after someone has been there for `enter_seconds` and AVAILABLE after it has been empty for `leave_seconds`, measured in wall-clock time so the behaviour does not depend on the frame rate. Detection gaps shorter than `presence_hold_seconds` (1 s) are ignored.
- **Analytics** (`backend/app/analytics.py`): every status change is stored in SQLite, together with every *run* (a stretch of time in which a video with tables was watched). Sessions are rebuilt from the events with the same rule as the live timer, from when guests arrive until they leave. Every run starts with all tables AVAILABLE, so time that nobody watched never counts as occupied.

## Tech stack

| Area | Technology |
|---|---|
| Camera simulation | MediaMTX (RTSP server) + ffmpeg, managed by the backend |
| Detection and tracking | Ultralytics YOLO (`yolo26s` by default, person class) + ByteTrack |
| Zones and annotation | supervision + OpenCV |
| Backend | FastAPI, Uvicorn, SQLite, loguru, pydantic-settings |
| Live delivery | MJPEG (video) and WebSocket (status and events) |
| Frontend | React, Vite, Tailwind CSS, react-konva (table editor), recharts (charts) |
| Tests | pytest, vitest |

## Getting started

### Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.10 or newer (tested with 3.14) | `python --version` |
| Node.js | 20.19+ or 22.12+ | `node --version` |
| ffmpeg + ffprobe | a recent build with libx264 | `ffmpeg -version` |
| git | any recent version | `git --version` |

MediaMTX is downloaded by a script. A CPU is enough; an NVIDIA GPU with CUDA is used automatically when available.

Install ffmpeg with `winget install Gyan.FFmpeg` on Windows (then open a new terminal) or `sudo apt install ffmpeg` on Debian/Ubuntu.

### Installation

```bash
git clone https://github.com/sanaulislamzihad/Restaurant-Table-Occupancy-Detection.git
cd Restaurant-Table-Occupancy-Detection
```

Create the Python environment and install the backend:

```powershell
# Windows (PowerShell)
py -3.14 -m venv .venv            # or: python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

If PowerShell refuses to run `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or call `.\.venv\Scripts\python.exe` directly.

**NVIDIA GPU (optional):** the default PyTorch wheel on Windows is CPU-only. Before installing the requirements, install the CUDA build of the `torch` and `torchvision` versions pinned in `backend/requirements.txt` with the command for your CUDA version from <https://pytorch.org/get-started/locally/>.

Optionally download MediaMTX and the dashboard packages now (the start script does both when they are missing):

```bash
python fake_camera/download_mediamtx.py      # into fake_camera/bin/, checksum verified
cd frontend
npm install
```

Settings work out of the box. To change them, copy `backend/.env.example` to `backend/.env` (and `frontend/.env.example` to `frontend/.env`) and edit the copy; see [Configuration](#configuration).

### Run

```text
run_all.bat          Windows: double-click, or run it from the project folder
./run_all.sh         Linux / macOS
```

This starts the backend (which starts MediaMTX itself) and the dashboard, and opens <http://localhost:5173>. On Windows both run in their own windows; close them or press `Ctrl+C` in them to stop. On Linux/macOS `Ctrl+C` stops both.

To run them by hand, use two terminals from the project folder, with the virtual environment active:

```bash
# Terminal 1: API on http://127.0.0.1:8000 (interactive docs at /docs)
cd backend
python -m app

# Terminal 2: dashboard on http://localhost:5173
cd frontend
npm run dev
```

`python -m app --video <id>` (or `DEFAULT_VIDEO_ID` in `backend/.env`) starts watching a video right away.

## Usage

1. **Videos:** drop a restaurant video on the upload area. Each video shows its thumbnail, duration, resolution and whether its tables are set up. **Start Stream** loops it as `rtsp://localhost:8554/cam1` and opens the Live Monitor (or Table Setup if the video has no tables yet). Starting another video switches the camera and the tables.
2. **Table Setup:** draw the tables once per camera view, on the live frame or a frame from the file (**Another frame** picks a different moment). Pink dots mark the point of each person that must be inside a table's outline, so draw each outline around the table **and its chairs**.
   - **Draw table**, click the corners, then click the first corner or press `Enter`. `Backspace` removes the last corner, `Esc` cancels.
   - Select a table to drag it or its corners. Double-click an edge to add a corner, right-click a corner to remove it, `Delete` removes the table. Rename or delete tables in the list.
   - **Suggest tables** adds outlines around the tables the detector recognises, grown over their chairs and seated people without overlapping. Review them before saving.
   - **Copy from video** reuses the tables of another recording of the same camera view.
   - Invalid outlines (too thin, too small, self-crossing) are marked red and block saving; overlaps are flagged. **Save** writes `backend/configs/<id>.json`, and a streaming video uses the new tables at once.
3. **Live Monitor:** annotated video (green = AVAILABLE, red = OCCUPIED, yellow = changing), a card per table with its status, people count and "occupied for mm:ss", tables available, connection state (LIVE / RECONNECTING / OFFLINE), detection speed and an event log.
4. **Analytics:** choose a video and a time range (15 minutes to all time) to see average occupancy, watched time, sessions, average session length, occupancy over time and a per-table breakdown. Live videos refresh every 10 seconds.

## Using a real CCTV camera

Every video or camera has a table config in `backend/configs/<id>.json`, and its `source` defines where frames come from. No code changes are needed:

1. Create a config for the camera, for example `backend/configs/entrance.json`:

   ```json
   {
     "video_id": "entrance",
     "source": "rtsp://user:password@192.168.1.20:554/stream1",
     "frame_width": 1920,
     "frame_height": 1080,
     "tables": [],
     "occupancy": { "confidence_threshold": 0.15, "enter_seconds": 5, "leave_seconds": 10, "reference_point": "bottom_center" }
   }
   ```

   Any source OpenCV/FFmpeg can open works: `rtsp://`, `http://`, a video file or a webcam index such as `0`. Passwords are hidden in the logs.
2. Start the backend on it: `python -m app --video entrance` (or set `DEFAULT_VIDEO_ID=entrance`).
3. Draw the tables on a live frame with `python backend/scripts/draw_tables.py entrance`, then restart the backend (or send them with `PUT /api/config/tables` while it runs).

The Live Monitor and Analytics work exactly as with the simulated camera. Polygons are stored in the pixel size given by `frame_width` and `frame_height` and are scaled when the live frames have another size.

## Configuration

All settings are in `backend/.env` (see `backend/.env.example`, which documents each one). The most important:

| Variable | Default | Purpose |
|---|---|---|
| `API_HOST`, `API_PORT` | `127.0.0.1`, `8000` | Where the API listens |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Browser origins allowed to call the API |
| `FAKE_CAMERA_RTSP_URL` | `rtsp://localhost:8554/cam1` | Where uploaded videos are published as a camera |
| `MANAGE_MEDIAMTX` | `true` | Start MediaMTX automatically when nothing listens on that port |
| `VIDEOS_DIR`, `CONFIGS_DIR`, `DATA_DIR` | `../fake_camera/videos`, `configs`, `data` | Videos, table configs, database and thumbnails |
| `MAX_UPLOAD_MB` | `500` | Largest accepted upload |
| `YOLO_MODEL` | `yolo26s.pt` | Detection model (downloaded into `MODELS_DIR` on first use) |
| `DEVICE` | `auto` | `auto` uses CUDA when available; or `cpu`, `cuda:0` |
| `YOLO_IMG_SIZE`, `CONFIDENCE_THRESHOLD` | `640`, `0.15` | Detection resolution and minimum person confidence |
| `DETECT_EVERY_N_FRAMES` | `1` | Run detection on every Nth frame |
| `ENTER_SECONDS`, `LEAVE_SECONDS` | `5`, `10` | Default occupancy delays for new table configs |
| `REFERENCE_POINT` | `bottom_center` | Point of a person's box that must be inside a table |
| `SOURCE_RECONNECT_SECONDS` | `3` | Wait before reconnecting to a dropped stream |
| `SOURCE_OUTAGE_SECONDS` | `30` | Stop counting occupancy after this long without frames |
| `LOG_DIR`, `LOG_ROTATION_MB`, `LOG_RETENTION_FILES` | `logs`, `10`, `5` | Rotating log files |

Each table config can override the occupancy settings for its video. The dashboard's `frontend/.env` sets `VITE_API_BASE_URL` (the backend address) and `FRONTEND_PORT` (which must be listed in `CORS_ORIGINS`).

## Performance

Intel Core i5-1235U laptop CPU, no GPU, 640x360 overhead restaurant footage, `YOLO_IMG_SIZE=640`, confidence 0.15:

| Model | Detection + tracking | In the running backend (20 fps video) | People found per frame |
|---|---|---|---|
| `yolo11n.pt` | 19.7 fps (51 ms) | ~18 detections/s | 8.0 |
| `yolo26n.pt` | 20.1 fps (50 ms) | | 7.5 |
| `yolo11s.pt` | 9.3 fps (108 ms) | | 15.0 |
| **`yolo26s.pt` (default)** | **9.0 fps (111 ms)** | **~8.5 detections/s** | **15.5** |

The default model finds about twice as many people as `yolo11n` in overhead footage, where seated guests are small and partly hidden. Occupancy needs only a few detections per second, and the video itself always streams at full frame rate. Choose `yolo11n.pt` for speed on weaker machines; with a fast model or a GPU, `DETECT_EVERY_N_FRAMES` can save power. To benchmark your own machine:

```bash
python backend/scripts/benchmark.py fake_camera/videos/restaurant.mp4 --models yolo11n.pt yolo26s.pt
```

## Reliability

- **Stream failures:** if ffmpeg stops, `/api/stream/status` reports the error, the Live Monitor shows RECONNECTING with a **Restart stream** button, and table cards are marked as the last known status.
- **Outages:** when no frames arrive for `SOURCE_OUTAGE_SECONDS`, occupancy stops being counted and starts over when the video returns.
- **Video switching:** switching videos loads the other table config; frames of the old video are never processed with the new tables.
- **Process management:** ffmpeg and MediaMTX are bound to the backend (a Job Object on Windows, a parent-death signal on Linux) and end with it, even after a crash.
- **Logging:** the console and `backend/logs/backend.log` (rotated every `LOG_ROTATION_MB`, the newest `LOG_RETENTION_FILES` kept), including the web server's messages.

## API reference

Interactive documentation is served at <http://127.0.0.1:8000/docs>.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Pipeline, model, source, MediaMTX and ffmpeg state, processing and stream FPS |
| POST | `/api/videos` | Upload a video (multipart field `file`) |
| GET | `/api/videos` | Uploaded videos with metadata, table count and streaming flag |
| GET | `/api/videos/limits` | Largest upload and accepted file types |
| GET | `/api/videos/{id}/thumbnail` | First frame as a JPEG |
| GET | `/api/videos/{id}/editor-frame?at=1` | A frame to draw on, with people's points and suggested table outlines |
| GET | `/api/videos/{id}/config` | Table config of a video |
| PUT | `/api/videos/{id}/tables` | Save table outlines of a video (applied live if it is streaming) |
| DELETE | `/api/videos/{id}` | Delete a video with its thumbnail, table config and analytics history |
| POST | `/api/stream/start` | Body `{"video_id": ...}`: stream that video as the camera |
| POST | `/api/stream/stop` | Stop the camera |
| GET | `/api/stream/status` | Current video and state: running, stopped or error |
| GET | `/api/stream` | Annotated live video (MJPEG, usable as `<img src>`) |
| GET | `/api/snapshot` | One raw JPEG frame of the live video |
| GET | `/api/tables` | Current status of every table |
| GET | `/api/events?limit=50` | Recent table status changes, newest first |
| GET | `/api/config` | Table config of the active video |
| PUT | `/api/config/tables` | Save table outlines of the active video (applied immediately) |
| GET | `/api/analytics?video_id=&since=&until=` | Per-table occupied time and sessions, and occupancy over time |
| GET | `/api/analytics/videos` | Videos with recorded watching time |
| WS | `/ws/status` | Pushes `{"type": "status", ...}` on every change and every second, and `{"type": "event", ...}` for each status change |

Videos copied into `fake_camera/videos/` by hand are registered at startup with their file name as ID (`restaurant.mp4` → `restaurant`).

## Development

### Tests

```bash
python -m pytest backend          # backend
cd frontend
npm test                          # dashboard helpers
npm run build                     # type check + production build
```

### Developer tools

<details>
<summary>Command-line tools for the camera, detection and tables</summary>

All commands run from the project folder with the virtual environment active.

**Simulated camera without the backend**

```powershell
fake_camera\start_camera.bat my_video.mp4      # Windows
./fake_camera/start_camera.sh my_video.mp4     # Linux / macOS
```

Streams a video from `fake_camera/videos/` (or any path) to `rtsp://localhost:8554/cam1` in real time, looping, and starts MediaMTX if needed. Open it in VLC (*Media → Open Network Stream*) or with `ffplay -rtsp_transport tcp rtsp://localhost:8554/cam1`. Do not run it while the backend streams a video: both would publish to the same URL.

**Preview a video source**

```bash
python backend/scripts/preview_source.py                                  # FAKE_CAMERA_RTSP_URL
python backend/scripts/preview_source.py fake_camera/videos/my_video.mp4  # a video file
python backend/scripts/preview_source.py 0                                # webcam 0
```

**Preview detection and tracking**

```bash
python backend/scripts/preview_detection.py fake_camera/videos/my_video.mp4
python backend/scripts/preview_detection.py --conf 0.5 --every 2
```

**Draw tables and watch occupancy without the dashboard**

```bash
python backend/scripts/draw_tables.py restaurant                                               # on the config's source
python backend/scripts/draw_tables.py restaurant --source fake_camera/videos/restaurant.mp4   # on a file
python backend/scripts/preview_occupancy.py restaurant
```

In `draw_tables.py`, left-click the corners of a table, right-click or `Enter` to finish it, `s` to save; `Backspace` undoes, `c` clears, `h` toggles the help and `q` quits without saving.

</details>

### Project structure

<details>
<summary>Folder layout</summary>

```text
.
├── run_all.bat / run_all.sh   # start the backend and the dashboard
├── docs/screenshots/          # images used in this README
├── fake_camera/
│   ├── download_mediamtx.py   # fetches the MediaMTX binary into bin/
│   ├── mediamtx.yml           # MediaMTX config (RTSP over TCP only)
│   ├── start_camera.py        # loops a video as a live RTSP stream (.bat / .sh launchers)
│   ├── bin/                   # MediaMTX executable (not in git)
│   └── videos/                # footage (not in git)
├── backend/
│   ├── app/
│   │   ├── __main__.py        # `python -m app`: runs the server
│   │   ├── main.py            # API routes, MJPEG stream, WebSocket
│   │   ├── pipeline.py        # live pipeline (frame + detection threads)
│   │   ├── stream_manager.py  # runs MediaMTX and ffmpeg
│   │   ├── process_guard.py   # child processes never outlive the backend
│   │   ├── videos.py          # uploads, metadata, thumbnails, delete
│   │   ├── db.py              # SQLite: events, runs and video metadata
│   │   ├── analytics.py       # sessions and occupancy statistics
│   │   ├── broadcaster.py     # pushes status and events to WebSocket clients
│   │   ├── settings.py        # typed settings from backend/.env
│   │   ├── logging_setup.py   # console + rotating log file
│   │   ├── sources.py         # FrameSource: file / stream / webcam reader
│   │   ├── detector.py        # PersonDetector: YOLO + ByteTrack
│   │   ├── schemas.py         # table config and API models
│   │   ├── config_store.py    # load / save configs/<video_id>.json
│   │   ├── occupancy.py       # per-table state machine
│   │   ├── geometry.py        # outline checks and suggested outlines
│   │   ├── scene_hints.py     # people, chairs and tables for the table editor
│   │   └── annotator.py       # draws tables, people and the status overlay
│   ├── scripts/               # preview, drawing and benchmark tools
│   ├── tests/                 # pytest suite
│   ├── configs/               # <video_id>.json table configs
│   ├── models/, data/, logs/  # weights, database, logs (not in git)
│   ├── requirements.txt       # pinned Python dependencies
│   └── .env.example           # configuration template
└── frontend/
    ├── src/api/               # REST client and response types
    ├── src/components/        # table editor canvas, charts, cards, upload, event log
    ├── src/hooks/             # live WebSocket status, clock
    ├── src/lib/               # formatting and polygon helpers
    ├── src/pages/             # Videos, Live Monitor, Table Setup, Analytics
    └── .env.example           # backend address and dashboard port
```

</details>

## License

This project is released under the [MIT License](LICENSE).

It builds on third-party software under its own licenses, most notably [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) (AGPL-3.0; commercial use needs an [Ultralytics license](https://www.ultralytics.com/license)), MediaMTX (MIT) and FFmpeg (LGPL/GPL, installed separately). Videos and model weights are not part of this repository.
