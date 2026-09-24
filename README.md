# Restaurant Table Occupancy Detection

Detects whether each table in a restaurant is **AVAILABLE** or **OCCUPIED** from a CCTV feed and shows it on a live web dashboard, with occupancy analytics.

There is no live camera yet, so uploaded CCTV footage is played as a looping **fake RTSP camera** (MediaMTX + ffmpeg, started and managed by the backend). The detection pipeline reads that RTSP stream exactly like a real camera, so moving to a real CCTV camera is a config change, not a code change.

## Screenshots

| Live Monitor | Table Setup |
|---|---|
| ![Live Monitor: annotated video and one card per table](docs/screenshots/live-monitor.jpg) | ![Table Setup: drawing table outlines on a frame](docs/screenshots/table-setup.jpg) |
| **Videos** | **Analytics** |
| ![Videos: upload and start a stream](docs/screenshots/videos.jpg) | ![Analytics: occupancy over time and per table](docs/screenshots/analytics.jpg) |

## Features

- **Upload restaurant videos** in the browser (mp4, avi, mov, mkv) and stream any of them as a live RTSP camera with one click.
- **Person detection and tracking** with Ultralytics YOLO and ByteTrack; every person keeps an ID across frames.
- **Per-table occupancy** with enter and leave delays, so a passer-by or someone who stands up for a moment does not flip a table.
- **Live Monitor:** annotated video, a card per table with an "occupied for" timer, tables available, connection state and an event log.
- **Table editor in the browser:** draw outlines, drag corners, rename, get outlines suggested from the detected tables, or copy them from another video. Changes apply to the live view at once.
- **Analytics:** occupied time, sessions and occupancy over time per table, counted only while the video is actually watched.
- **Robust:** ffmpeg and MediaMTX never outlive the backend, a stopped stream shows up on the dashboard with a restart button, and the pipeline reconnects by itself.
- **Configuration, not code:** all paths, ports, URLs and tuning values are in `backend/.env` and per-video JSON configs. Runs on Windows and Linux, on the CPU or on an NVIDIA GPU when CUDA is available.

## How it works

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

- **Pipeline** (`backend/app/pipeline.py`): a frame thread reads every frame, draws the latest results on it and serves it as MJPEG, so the video stays smooth (20 fps). A detection thread takes the newest frame and runs YOLO, tracking and the occupancy state machine as fast as the CPU allows. The pipeline only gets frames; it does not know whether they come from a file, an RTSP stream or a webcam.
- **Occupancy** (`backend/app/occupancy.py`): a person is at a table when the bottom centre of their box (or its centre) is inside the table's outline. A table becomes OCCUPIED after someone has been there for `enter_seconds` and AVAILABLE after it has been empty for `leave_seconds`, measured on the clock so it behaves the same at any frame rate. Detection gaps shorter than `presence_hold_seconds` (1 s) are ignored.
- **Analytics** (`backend/app/analytics.py`): every status change is stored in SQLite, and so is every *run* (a stretch of time a video with tables was watched). Sessions are rebuilt from the events with the same rule as the Live Monitor's timer: from when the guests arrived until they left. Every run starts with all tables AVAILABLE, and time the video was not watched never counts as occupied.

## Tech stack

| Part | Tool |
|---|---|
| Fake camera | MediaMTX (RTSP server) + ffmpeg, managed by the backend |
| Detection + tracking | Ultralytics YOLO (`yolo26s` by default, person class) + ByteTrack |
| Zones and drawing | supervision + OpenCV |
| Backend | FastAPI + Uvicorn, SQLite, loguru |
| Live video / live status | MJPEG stream / WebSocket |
| Frontend | React + Vite + Tailwind CSS, react-konva for the table editor, recharts for the charts |

## Requirements

| Tool | Version | Check |
|---|---|---|
| Python | 3.10 or newer (tested with 3.14.2) | `python --version` |
| Node.js | 20.19+ or 22.12+ (needed by Vite 8) | `node --version` |
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

This downloads the latest MediaMTX release for your OS into `fake_camera/bin/`, verifies its SHA-256 checksum and runs `mediamtx --version`. Add `--tag v1.21.1` to get a specific release. (`run_all` does this by itself when MediaMTX is missing.)

### 5. Configuration (optional)

```bash
copy backend\.env.example backend\.env      # Windows
cp backend/.env.example backend/.env        # Linux / macOS
```

Without a `backend/.env` the values of `backend/.env.example` are used; each value is explained there. All paths, ports, URLs and tuning values live in `backend/.env` or in the per-video table configs in `backend/configs/`. The dashboard reads `frontend/.env` the same way (copy `frontend/.env.example`): `VITE_API_BASE_URL` is the backend address and `FRONTEND_PORT` the dashboard's port, which must be in `CORS_ORIGINS` in `backend/.env`.

### 6. Dashboard packages

```bash
cd frontend
npm install
```

(`run_all` also installs them when some are missing or out of date.)

## Running

### One command

```text
run_all.bat          Windows: double-click it, or run it from the project folder
./run_all.sh         Linux / macOS
```

It starts the backend (which starts MediaMTX by itself) and the dashboard, and opens <http://localhost:5173> in the browser. On Windows each runs in its own window: close both windows (or press `Ctrl+C` in them) to stop. On Linux/macOS `Ctrl+C` stops both.

### By hand

Backend, from the `backend` folder with the virtual environment active:

```bash
cd backend
python -m app
```

Dashboard, in a second terminal:

```bash
cd frontend
npm run dev
```

The backend serves the API on `API_HOST`:`API_PORT` (default <http://127.0.0.1:8000>, interactive docs at <http://127.0.0.1:8000/docs>). It starts MediaMTX unless an RTSP server already listens on the port of `FAKE_CAMERA_RTSP_URL` (set `MANAGE_MEDIAMTX=false` to never start it). `Ctrl+C` stops everything within a second, and ffmpeg and MediaMTX are tied to the backend process, so they never keep running after it, even if it crashes or is killed. `python -m app --video <id>` (or `DEFAULT_VIDEO_ID` in `backend/.env`) starts watching that video right away.

## How to use

1. **Videos:** drop a restaurant video on the upload area (type and size are checked first; a progress bar shows the upload). Each video shows its thumbnail, duration, resolution and whether its tables are set up. Press **Start Stream**: the video loops as `rtsp://localhost:8554/cam1`, and the dashboard opens the Live Monitor, or Table Setup if the video has no tables yet. Starting another video switches the camera and the tables; **Stop** stops it.
2. **Table Setup:** draw the tables once per camera view, on a frame of the video (the live frame while it streams; **Another frame** shows a different moment). Pink dots mark people: the point that must be inside a table's outline for that table to count them, so draw each outline around the table **and its chairs**.
   - **Draw table**, click the corners, then click the first corner or press `Enter`. `Backspace` removes the last corner, `Esc` cancels.
   - Click a table to select it, then drag it or its corners. Double-click an edge to add a corner, right-click a corner to remove it, `Delete` removes the table. Rename or delete tables in the list on the right.
   - **Suggest tables** adds outlines around the tables the detector recognises in the frame (grown over their chairs and seated people, without overlapping). Check them: adjust the corners, delete wrong ones and draw the missed ones.
   - **Copy from video** takes the tables of another video, scaled to this one, for a new recording of the same camera view.
   - Outlines that are too thin, too small or cross themselves are marked red and must be fixed before saving; overlapping outlines get a warning. **Save** stores them in `backend/configs/<id>.json`; a streaming video uses them right away.
3. **Live Monitor:** the annotated video (green = AVAILABLE, red = OCCUPIED, yellow = waiting to change), a card per table with its status, people count and "occupied for mm:ss", "N / M tables available", the connection state (LIVE / RECONNECTING / OFFLINE), the detection speed and a log of tables becoming occupied or available. It reconnects by itself when the backend restarts.
4. **Analytics:** pick a video and a time range (15 min to all time): average occupancy, watched time, number of sessions (groups of guests) and average session length; the share of tables occupied over time (hover for details, or *Show as table*); and per table its occupancy, occupied time, sessions, average and longest session. While the video is live it updates every 10 seconds.

## Using a real CCTV camera

Every video or camera has a table config in `backend/configs/<id>.json`, and its `source` is where the frames come from. No code changes are needed:

1. Create a config for the camera, for example `backend/configs/entrance.json`, by copying an existing one and changing `video_id` and `source`:

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

   Any address OpenCV/FFmpeg can open works: `rtsp://`, `http://`, a video file path, or a webcam number such as `0`. Passwords in the URL are hidden in the logs.
2. Start the backend watching it: `python -m app --video entrance` (or `DEFAULT_VIDEO_ID=entrance` in `backend/.env`).
3. Draw the tables on a live frame of the camera: `python backend/scripts/draw_tables.py entrance` (see *Developer tools*), then restart the backend, or save them with `PUT /api/config/tables` while it runs.

The Live Monitor and Analytics work the same as with the fake camera. If the camera drops out, the pipeline reconnects by itself (`SOURCE_RECONNECT_SECONDS`), and a camera that stays down longer than `SOURCE_OUTAGE_SECONDS` stops occupancy from being counted until it is back. To use the uploaded videos with a different RTSP server, change `FAKE_CAMERA_RTSP_URL` instead.

## Performance

Measured on an Intel i5-1235U laptop CPU (no GPU), 640x360 overhead restaurant CCTV footage, `YOLO_IMG_SIZE=640`, confidence 0.15:

| Model | Detection + tracking alone | In the running backend (video at 20 fps) | People found per frame |
|---|---|---|---|
| `yolo11n.pt` | 19.7 fps (51 ms) | ~18 detections/s | 8.0 |
| `yolo26n.pt` | 20.1 fps (50 ms) | | 7.5 |
| `yolo11s.pt` | 9.3 fps (108 ms) | | 15.0 |
| **`yolo26s.pt` (default)** | **9.0 fps (111 ms)** | **~8.5 detections/s** | **15.5** |

The default finds about twice as many people as `yolo11n`, which matters in overhead footage where seated people are small and partly hidden; the ones it still misses are mostly behind the counter or tables. Occupancy only needs a few detections per second, so ~8 per second on a CPU is plenty, and the video itself always streams at its full frame rate. Set `YOLO_MODEL=yolo11n.pt` in `backend/.env` for a faster but less accurate model. With a fast model or a GPU, `DETECT_EVERY_N_FRAMES=2` (or more) runs detection on fewer frames to save power. The live detection speed is shown on the Live Monitor and logged every 10 seconds.

To measure on your machine (weights missing from `backend/models/` are downloaded first):

```bash
python backend/scripts/benchmark.py fake_camera/videos/restaurant.mp4 --models yolo11n.pt yolo26s.pt
```

## Robustness

- **ffmpeg stops** (crash or killed): `/api/stream/status` reports `error` with ffmpeg's last messages, the Live Monitor shows RECONNECTING, a red banner with **Restart stream**, and the table cards greyed out as "last known status". Restarting (there, or **Start Stream** on the Videos page) brings it back.
- **The video stops arriving** for longer than `SOURCE_OUTAGE_SECONDS` (default 30 s): occupancy is no longer counted, and it starts over (all tables AVAILABLE) when frames come back, so Analytics never count time nobody watched.
- **Switching videos** loads the other video's table config; the pipeline reconnects by itself and never mixes frames of the old video with the new tables.
- **No orphan processes:** on Windows ffmpeg and MediaMTX run in a Job Object, on Linux they get a parent-death signal, so they end with the backend even after a crash.
- **Logs:** everything, including the web server's messages, goes to the console and to `backend/logs/backend.log`. A new file is started every `LOG_ROTATION_MB` (10 MB) and the newest `LOG_RETENTION_FILES` (5) are kept.

## API

Interactive docs: <http://127.0.0.1:8000/docs>.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Pipeline, model, source, MediaMTX and ffmpeg state, processing and stream FPS |
| POST | `/api/videos` | Upload a video (multipart field `file`) |
| GET | `/api/videos` | Uploaded videos with metadata, table count and streaming flag |
| GET | `/api/videos/limits` | Largest upload and accepted file types |
| GET | `/api/videos/{id}/thumbnail` | First frame as a JPEG |
| GET | `/api/videos/{id}/editor-frame?at=1` | A frame to draw on (live if the video is streaming, else from the file at `at` seconds) with people's points and suggested table outlines |
| GET | `/api/videos/{id}/config` | Table config of any video |
| PUT | `/api/videos/{id}/tables` | Save table outlines of any video; if it is streaming, the live view uses them at once |
| DELETE | `/api/videos/{id}` | Delete a video, its thumbnail, table config and analytics history (stops its stream first) |
| POST | `/api/stream/start` | Body `{"video_id": ...}`: stream that video as the fake CCTV camera (404 lists the valid ids) |
| POST | `/api/stream/stop` | Stop the fake camera |
| GET | `/api/stream/status` | Current video; running, stopped or error (with ffmpeg's last messages) |
| GET | `/api/stream` | Annotated live video (MJPEG, usable as `<img src>`) |
| GET | `/api/snapshot` | One raw JPEG frame of the live video |
| GET | `/api/tables` | Current status of every table |
| GET | `/api/events?limit=50` | Recent table status changes, newest first |
| GET | `/api/config` | Table config of the active video |
| PUT | `/api/config/tables` | Save new table outlines of the active video; the live view uses them at once |
| GET | `/api/analytics?video_id=&since=&until=` | Per-table occupied time and sessions, and occupancy over time (defaults: the live video, all its recorded time) |
| GET | `/api/analytics/videos` | Videos with recorded streaming time |
| WS | `/ws/status` | Pushes `{"type": "status", "data": ...}` on every change and every second, and `{"type": "event", "data": ...}` for each table status change |

Videos copied into `fake_camera/videos/` by hand are added when the backend starts, with their file name as ID (`restaurant.mp4` → `restaurant`), so an existing `backend/configs/restaurant.json` applies to them.

## Table config

```json
{
  "video_id": "restaurant",
  "source": "rtsp://localhost:8554/cam1",
  "frame_width": 640,
  "frame_height": 360,
  "tables": [
    { "id": "T1", "name": "Table 1", "polygon": [[20, 250], [140, 250], [170, 355], [20, 355]] }
  ],
  "occupancy": {
    "confidence_threshold": 0.15,
    "enter_seconds": 5,
    "leave_seconds": 10,
    "reference_point": "bottom_center",
    "presence_hold_seconds": 1.0
  }
}
```

Polygons are in pixels of a `frame_width` x `frame_height` frame and are scaled automatically when the live frames have another size. New configs take their `occupancy` values from `ENTER_SECONDS`, `LEAVE_SECONDS`, `REFERENCE_POINT` and `CONFIDENCE_THRESHOLD` in `backend/.env`; edit the file to change them for one video (saving tables in the editor keeps them).

## Developer tools

All commands run from the project folder with the virtual environment active.

#### Fake CCTV camera without the backend

```powershell
fake_camera\start_camera.bat my_video.mp4      # Windows
./fake_camera/start_camera.sh my_video.mp4     # Linux / macOS
```

Streams a video from `fake_camera/videos/` (or any path) to `rtsp://localhost:8554/cam1` in real time, looping forever, and starts MediaMTX if needed. Without a file name it lists the videos. Open the stream in VLC (*Media → Open Network Stream*) or with `ffplay -rtsp_transport tcp rtsp://localhost:8554/cam1`. Do not run it while the backend streams a video: both would publish to the same URL. If Windows asks for firewall access for MediaMTX, **Cancel** keeps the camera reachable from this computer only.

#### Preview a video source

```bash
python backend/scripts/preview_source.py                                  # FAKE_CAMERA_RTSP_URL
python backend/scripts/preview_source.py fake_camera/videos/my_video.mp4  # a video file
python backend/scripts/preview_source.py 0                                # webcam 0
```

Shows what `FrameSource` (`backend/app/sources.py`) reads, with its status (LIVE, RECONNECTING, ...), frame rate and size. Stop the camera while previewing: the status switches to RECONNECTING and the picture comes back by itself. `q` or `Esc` quits.

#### Preview person detection and tracking

```bash
python backend/scripts/preview_detection.py                                  # the fake camera stream
python backend/scripts/preview_detection.py fake_camera/videos/my_video.mp4  # a video file
python backend/scripts/preview_detection.py --conf 0.5 --every 2             # stricter, detect every 2nd frame
```

Each person is drawn with a box, `#ID confidence` and a short trail; a seated person should keep the same ID.

#### Draw the tables and watch occupancy without the dashboard

```bash
python backend/scripts/draw_tables.py restaurant                                               # on the config's source
python backend/scripts/draw_tables.py restaurant --source fake_camera/videos/restaurant.mp4   # on a file
python backend/scripts/preview_occupancy.py restaurant                                         # watch it live
```

`draw_tables.py` opens a frame with every person marked by a pink dot. Left-click the corners of a table, right-click or `Enter` to finish it, repeat for every table, then `s` saves. `Backspace` undoes, `c` clears everything, `h` hides the help and `q` quits without saving. Running it again loads the saved tables for editing. `preview_occupancy.py` shows the tables in green / red / yellow and prints every status change.

## Tests

```bash
python -m pytest backend          # backend
cd frontend && npm test           # dashboard helpers
cd frontend && npm run build      # type check + production build
```

## Project structure

```text
.
├── run_all.bat / run_all.sh   # start the backend and the dashboard
├── docs/screenshots/          # images used in this README
├── fake_camera/
│   ├── download_mediamtx.py   # fetches the MediaMTX binary into bin/
│   ├── mediamtx.yml           # MediaMTX config (RTSP over TCP only)
│   ├── start_camera.py        # loops a video as a live RTSP stream (.bat / .sh launchers)
│   ├── bin/                   # MediaMTX executable (gitignored)
│   └── videos/                # footage (gitignored)
├── backend/
│   ├── app/                   # FastAPI application
│   │   ├── __main__.py        # `python -m app`: runs the server
│   │   ├── main.py            # API routes, MJPEG stream, WebSocket
│   │   ├── pipeline.py        # live pipeline (frame + detection threads)
│   │   ├── stream_manager.py  # runs MediaMTX and ffmpeg (the fake camera)
│   │   ├── process_guard.py   # child processes never outlive the backend
│   │   ├── videos.py          # uploads, metadata, thumbnails, delete
│   │   ├── db.py              # SQLite: events, streaming runs and video metadata
│   │   ├── analytics.py       # sessions and occupancy statistics from the events
│   │   ├── broadcaster.py     # pushes status/events to WebSocket clients
│   │   ├── settings.py        # typed settings from backend/.env
│   │   ├── logging_setup.py   # console + rotating log file
│   │   ├── sources.py         # FrameSource: file / stream / webcam reader
│   │   ├── detector.py        # PersonDetector: YOLO + ByteTrack
│   │   ├── schemas.py         # table config and API models
│   │   ├── config_store.py    # load / save configs/<video_id>.json
│   │   ├── occupancy.py       # per-table AVAILABLE / OCCUPIED state machine
│   │   ├── geometry.py        # checks that a table outline is usable, suggested outlines
│   │   ├── scene_hints.py     # finds people, chairs and tables in a frame for the table editor
│   │   └── annotator.py       # draws tables, people and the status overlay
│   ├── scripts/               # preview_source, preview_detection, draw_tables, preview_occupancy, benchmark
│   ├── models/                # YOLO weights, downloaded on first use (gitignored)
│   ├── data/                  # SQLite database and thumbnails (gitignored)
│   ├── logs/                  # backend.log (gitignored)
│   ├── configs/               # <video_id>.json table configs
│   ├── tests/                 # pytest tests
│   ├── requirements.txt       # pinned Python dependencies
│   └── .env.example           # configuration template
└── frontend/                  # React + Vite + Tailwind dashboard
    ├── src/api/               # REST client and response types
    ├── src/hooks/             # live WebSocket status, clock
    ├── src/components/        # table editor canvas, charts, table card, video card, upload, event log, ...
    ├── src/lib/               # formatting and polygon helpers
    ├── src/pages/             # Videos, Live Monitor, Table Setup, Analytics
    └── .env.example           # backend address and dashboard port
```
