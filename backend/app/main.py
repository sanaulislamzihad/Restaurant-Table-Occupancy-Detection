"""FastAPI app: videos, fake-camera control, MJPEG live stream, status and WebSocket.

Start it with ``python -m app`` from the backend folder (see app/__main__.py);
the API docs are then at http://<API_HOST>:<API_PORT>/docs.
"""

from __future__ import annotations

import asyncio
import base64
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import cv2
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from loguru import logger
from pydantic import ValidationError
from starlette.types import ASGIApp, Receive, Scope, Send

from app.broadcaster import Broadcaster
from app.config_store import ConfigError, ConfigStore, build_table_config
from app.db import Database
from app.pipeline import Detector, Pipeline
from app.scene_hints import SceneAnalyzer, SceneHints
from app.schemas import (
    EditorFrameOut,
    EventOut,
    HealthOut,
    StreamStartIn,
    StreamStatusOut,
    TableConfig,
    TablesUpdate,
    TableStatusOut,
    UploadLimitsOut,
    VideoOut,
)
from app.settings import Settings, get_settings
from app.stream_manager import StreamManager
from app.videos import ALLOWED_EXTENSIONS, VideoError, VideoLibrary, read_frame

# Set when the server is asked to stop (Ctrl+C), so endless MJPEG streams and
# WebSocket loops end and the shutdown does not hang on open browser tabs.
shutdown_requested = threading.Event()

MJPEG_BOUNDARY = "frame"
DB_FILENAME = "app.db"
UPLOAD_PATH = "/api/videos"
MULTIPART_OVERHEAD_BYTES = 64 * 1024  # form headers around the file in an upload
EDITOR_JPEG_QUALITY = 90


async def mjpeg_parts(pipeline: Pipeline) -> AsyncIterator[bytes]:
    """Yield each new annotated JPEG as one part of a multipart/x-mixed-replace stream."""
    last_seq = -1
    while not shutdown_requested.is_set():
        seq, jpeg = pipeline.latest_jpeg()
        if jpeg is None or seq == last_seq:
            await asyncio.sleep(0.02)
            continue
        last_seq = seq
        header = f"--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
        yield header.encode() + jpeg + b"\r\n"


class UploadSizeLimit:
    """Refuse too-large uploads from their Content-Length, before the body is read."""

    def __init__(self, app: ASGIApp, path: str, max_bytes: int, max_mb: int) -> None:
        self.app, self.path, self.max_bytes, self.max_mb = app, path, max_bytes, max_mb

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"] == self.path:
            length = dict(scope["headers"]).get(b"content-length", b"")
            if length.isdigit() and int(length) > self.max_bytes + MULTIPART_OVERHEAD_BYTES:
                response = JSONResponse({"detail": f"The file is larger than {self.max_mb} MB."}, status_code=413)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(
    settings: Settings | None = None,
    detector_factory: Callable[[Settings], Detector] | None = None,
    popen: Callable[..., Any] | None = None,
    hints_factory: Callable[[Settings], SceneHints] | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own settings, fake detectors and a fake process launcher."""
    settings = settings or get_settings()
    hints_factory = hints_factory or SceneAnalyzer.from_settings
    hints_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = ConfigStore(settings.configs_dir)
        db = Database(settings.data_dir / DB_FILENAME)
        library = VideoLibrary(settings, db, store)
        await asyncio.to_thread(library.import_existing)
        streams = StreamManager(settings, **({"popen": popen} if popen else {}))
        broadcaster = Broadcaster()
        pipeline = Pipeline(settings, store, db, broadcaster,
                            **({"detector_factory": detector_factory} if detector_factory else {}))
        app.state.store, app.state.db, app.state.library = store, db, library
        app.state.streams, app.state.broadcaster, app.state.pipeline = streams, broadcaster, pipeline
        app.state.hints = None  # loaded when the table editor first asks for it
        shutdown_requested.clear()

        await asyncio.to_thread(streams.ensure_mediamtx)
        default_video = settings.default_video_id or None
        pipeline.start(default_video)
        if default_video and library.get(default_video):  # an uploaded video: stream it as well
            await asyncio.to_thread(streams.start, default_video, library.path(default_video))
        logger.info("API ready at http://{}:{}/docs", settings.api_host, settings.api_port)
        try:
            yield
        finally:
            logger.info("Shutting down")
            shutdown_requested.set()
            pipeline.stop()
            streams.shutdown()
            db.close()
            logger.info("Shutdown complete")

    app = FastAPI(
        title="Restaurant Table Occupancy",
        description="Live table occupancy from a CCTV stream: videos, fake camera, MJPEG video, "
                    "table status, events and table config.",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(UploadSizeLimit, path=UPLOAD_PATH, max_bytes=settings.max_upload_mb * 1024 * 1024,
                       max_mb=settings.max_upload_mb)

    # ------------------------------------------------------------------ helpers

    def config_of(request: Request, video_id: str) -> TableConfig | None:
        try:
            return request.app.state.store.load(video_id)
        except (ValidationError, ValueError):
            return None

    def video_out(request: Request, video: dict) -> VideoOut:
        streams: StreamManager = request.app.state.streams
        config = config_of(request, video["id"])
        return VideoOut(
            id=video["id"],
            name=video["original_name"],
            filename=video["filename"],
            size_bytes=video["size_bytes"],
            duration_seconds=video["duration_seconds"],
            width=video["width"],
            height=video["height"],
            fps=video["fps"],
            codec=video["codec"],
            uploaded_at=video["uploaded_at"],
            has_tables=bool(config and config.tables),
            table_count=len(config.tables) if config else 0,
            is_streaming=streams.state.value == "running" and streams.video_id == video["id"],
            thumbnail_url=f"/api/videos/{video['id']}/thumbnail",
        )

    def scene_hints(request: Request) -> SceneHints:
        with hints_lock:  # two editor tabs must not load the model twice
            if request.app.state.hints is None:
                request.app.state.hints = hints_factory(settings)
            return request.app.state.hints

    def stream_status(request: Request) -> StreamStatusOut:
        status = request.app.state.streams.status()
        if status.video_id is None:
            return status
        video = request.app.state.library.get(status.video_id)
        config = config_of(request, status.video_id)
        return status.model_copy(update={
            "video_name": video["original_name"] if video else None,
            "has_tables": bool(config and config.tables),
        })

    # ------------------------------------------------------------------ system

    @app.get("/api/health", response_model=HealthOut, tags=["system"])
    def health(request: Request) -> HealthOut:
        """Pipeline, model, source, MediaMTX and ffmpeg state, and the processing speed."""
        pipeline: Pipeline = request.app.state.pipeline
        stream = request.app.state.streams.status()
        return HealthOut(
            pipeline_running=pipeline.running,
            model=pipeline.model_state,
            device=pipeline.device,
            source=pipeline.source_label,
            processing_fps=pipeline.processing_fps,
            stream_fps=pipeline.stream_fps,
            video_id=pipeline.video_id,
            mediamtx=stream.mediamtx,
            mediamtx_managed=stream.mediamtx_managed,
            ffmpeg=stream.state,
        )

    # ------------------------------------------------------------------ videos

    @app.post(UPLOAD_PATH, response_model=VideoOut, status_code=201, tags=["videos"],
              responses={413: {"description": "Too large"}, 415: {"description": "Not mp4/avi/mov/mkv"},
                         422: {"description": "Not a readable video"}})
    async def upload_video(request: Request, file: UploadFile = File(...)) -> VideoOut:
        """Upload a video (mp4, avi, mov or mkv, up to MAX_UPLOAD_MB)."""
        try:
            video = await request.app.state.library.save_upload(file)
        except VideoError as error:
            raise HTTPException(error.status_code, str(error)) from error
        finally:
            await file.close()
        return video_out(request, video)

    @app.get("/api/videos", response_model=list[VideoOut], tags=["videos"])
    def list_videos(request: Request) -> list[VideoOut]:
        """Uploaded videos with their metadata, newest first."""
        return [video_out(request, video) for video in request.app.state.library.list()]

    @app.get("/api/videos/limits", response_model=UploadLimitsOut, tags=["videos"])
    def upload_limits() -> UploadLimitsOut:
        """Largest upload and accepted file types."""
        return UploadLimitsOut(max_upload_mb=settings.max_upload_mb, extensions=list(ALLOWED_EXTENSIONS))

    @app.get("/api/videos/{video_id}/thumbnail", tags=["videos"], response_class=FileResponse,
             responses={200: {"content": {"image/jpeg": {}}}, 404: {"description": "No such video"}})
    def video_thumbnail(request: Request, video_id: str) -> FileResponse:
        """First frame of the video as a small JPEG."""
        path = request.app.state.library.thumbnail_path(video_id)
        if path is None:
            raise HTTPException(404, "No thumbnail for this video.")
        return FileResponse(path, media_type="image/jpeg")

    @app.delete("/api/videos/{video_id}", status_code=204, tags=["videos"],
                responses={404: {"description": "No such video"}})
    def delete_video(request: Request, video_id: str) -> Response:
        """Delete a video with its thumbnail and table config (stops its stream first)."""
        library: VideoLibrary = request.app.state.library
        streams: StreamManager = request.app.state.streams
        pipeline: Pipeline = request.app.state.pipeline
        if library.get(video_id) is None:
            raise HTTPException(404, "No such video.")
        if streams.video_id == video_id:
            streams.stop()  # the file cannot be deleted while ffmpeg has it open
        if pipeline.video_id == video_id:
            pipeline.activate(None)
        library.delete(video_id)
        return Response(status_code=204)

    # ------------------------------------------------------------------ fake camera

    @app.post("/api/stream/start", response_model=StreamStatusOut, tags=["stream"],
              responses={404: {"description": "No such video"}})
    def start_stream(request: Request, body: StreamStartIn) -> StreamStatusOut:
        """Stream a video as the fake CCTV camera and watch it with its table config."""
        library: VideoLibrary = request.app.state.library
        path = library.path(body.video_id)
        if path is None:
            available = ", ".join(video["id"] for video in library.list()) or "none, upload one first"
            raise HTTPException(404, f"No video with id '{body.video_id}'. Available ids: {available}.")
        streams: StreamManager = request.app.state.streams
        streams.stop()  # the old video stops publishing before the pipeline switches to the new tables
        request.app.state.pipeline.activate(body.video_id)
        streams.start(body.video_id, path)
        return stream_status(request)

    @app.post("/api/stream/stop", response_model=StreamStatusOut, tags=["stream"])
    def stop_stream(request: Request) -> StreamStatusOut:
        """Stop the fake camera (MediaMTX keeps running); the live view goes idle."""
        streams: StreamManager = request.app.state.streams
        pipeline: Pipeline = request.app.state.pipeline
        if streams.video_id is not None and pipeline.video_id == streams.video_id:
            pipeline.activate(None)  # first, so the reader does not report the stream as lost
        streams.stop()
        return stream_status(request)

    @app.get("/api/stream/status", response_model=StreamStatusOut, tags=["stream"])
    def get_stream_status(request: Request) -> StreamStatusOut:
        """Current video and whether ffmpeg is running, stopped or failed."""
        return stream_status(request)

    # ------------------------------------------------------------------ live view

    @app.get("/api/stream", tags=["live"], response_class=StreamingResponse,
             responses={200: {"content": {f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}": {}}}})
    async def stream(request: Request) -> StreamingResponse:
        """Annotated live video as MJPEG. Use it directly: `<img src="/api/stream">`."""
        return StreamingResponse(
            mjpeg_parts(request.app.state.pipeline),
            media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/snapshot", tags=["live"], response_class=Response,
             responses={200: {"content": {"image/jpeg": {}}}, 503: {"description": "No frame yet"}})
    def snapshot(request: Request) -> Response:
        """One raw (not annotated) frame as JPEG, e.g. as the background of the table editor."""
        jpeg = request.app.state.pipeline.snapshot_jpeg()
        if jpeg is None:
            raise HTTPException(503, "No frame has been received from the camera yet.")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/tables", response_model=list[TableStatusOut], tags=["live"])
    def tables(request: Request) -> list[TableStatusOut]:
        """Current status of every table."""
        return request.app.state.pipeline.status().tables

    @app.get("/api/events", response_model=list[EventOut], tags=["live"])
    def events(
        request: Request,
        limit: int = Query(50, ge=1, le=1000),
        video_id: str | None = Query(None, description="only events of this video"),
    ) -> list[dict]:
        """Recent table status changes, newest first."""
        return request.app.state.db.recent_events(limit, video_id)

    # ------------------------------------------------------------------ table config

    @app.get("/api/config", response_model=TableConfig, tags=["config"],
             responses={404: {"description": "The active video has no table config yet"}})
    def config(request: Request) -> TableConfig:
        """Table config of the active video."""
        pipeline: Pipeline = request.app.state.pipeline
        current = pipeline.current_config()
        if current is None:
            name = pipeline.video_id or "the current video"
            raise HTTPException(404, f"No table config for {name} yet. Draw the tables first.")
        return current

    @app.put("/api/config/tables", response_model=TableConfig, tags=["config"],
             responses={422: {"description": "Invalid tables, or no active video"}})
    def put_tables(request: Request, update: TablesUpdate) -> TableConfig:
        """Save new table outlines for the active video; the live view uses them at once."""
        try:
            return request.app.state.pipeline.update_tables(update)
        except ConfigError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/videos/{video_id}/config", response_model=TableConfig, tags=["config"],
             responses={404: {"description": "No such video, or no table config yet"}})
    def video_config(request: Request, video_id: str) -> TableConfig:
        """Table config of any uploaded video."""
        if request.app.state.library.get(video_id) is None:
            raise HTTPException(404, "No such video.")
        config = config_of(request, video_id)
        if config is None:
            raise HTTPException(404, "This video has no table config yet.")
        return config

    @app.put("/api/videos/{video_id}/tables", response_model=TableConfig, tags=["config"],
             responses={404: {"description": "No such video"}, 422: {"description": "Invalid tables"}})
    def put_video_tables(request: Request, video_id: str, update: TablesUpdate) -> TableConfig:
        """Save table outlines for any video. If it is the live video, the live view uses them at once."""
        if request.app.state.library.get(video_id) is None:
            raise HTTPException(404, "No such video.")
        pipeline: Pipeline = request.app.state.pipeline
        try:
            if pipeline.video_id == video_id:
                return pipeline.update_tables(update)
            config = build_table_config(video_id, update, config_of(request, video_id),
                                        settings.fake_camera_rtsp_url, settings)
        except ConfigError as error:
            raise HTTPException(422, str(error)) from error
        request.app.state.store.save(config)
        logger.info("Saved {} table(s) for {}", len(config.tables), video_id)
        return config

    @app.get("/api/videos/{video_id}/editor-frame", response_model=EditorFrameOut, tags=["config"],
             responses={404: {"description": "No such video"}, 422: {"description": "No frame could be read"}})
    def editor_frame(
        request: Request,
        video_id: str,
        at: float = Query(1.0, ge=0, description="position in the video file, in seconds"),
        live: bool = Query(True, description="use the live frame when this video is streaming"),
        hints: bool = Query(True, description="also find people and suggest table outlines"),
    ) -> EditorFrameOut:
        """A frame to draw the tables on, with people's reference points and suggested table outlines."""
        path = request.app.state.library.path(video_id)
        if path is None:
            raise HTTPException(404, "No such video.")
        pipeline: Pipeline = request.app.state.pipeline
        frame = pipeline.latest_frame() if live and pipeline.video_id == video_id else None
        is_live = frame is not None
        if frame is None:
            frame = read_frame(path, at)
        if frame is None:
            raise HTTPException(422, "No frame could be read from this video.")
        encoded, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, EDITOR_JPEG_QUALITY])
        if not encoded:
            raise HTTPException(422, "The frame could not be encoded.")

        people: list = []
        suggested: list = []
        hints_error = None
        if hints:
            config = config_of(request, video_id)
            occupancy = config.occupancy if config else None
            try:
                people, suggested = scene_hints(request).analyze(
                    frame,
                    person_confidence=occupancy.confidence_threshold if occupancy else settings.confidence_threshold,
                    reference_point=occupancy.reference_point if occupancy else settings.reference_point,
                )
            except Exception as error:  # the editor still works without hints
                logger.warning("Could not find people and tables in the frame: {}", error)
                hints_error = f"People and tables could not be found: {error}"

        return EditorFrameOut(
            video_id=video_id,
            image="data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii"),
            frame_width=frame.shape[1],
            frame_height=frame.shape[0],
            live=is_live,
            at_seconds=None if is_live else at,
            people=people,
            suggested_tables=suggested,
            hints_error=hints_error,
        )

    # ------------------------------------------------------------------ WebSocket

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket) -> None:
        """Pushes {"type": "status", "data": ...} on every change (and each second)
        and {"type": "event", "data": ...} for every table status change."""
        pipeline: Pipeline = websocket.app.state.pipeline
        broadcaster: Broadcaster = websocket.app.state.broadcaster
        await websocket.accept()
        queue = broadcaster.subscribe()
        try:
            await websocket.send_json({"type": "status", "data": pipeline.status().model_dump(mode="json")})
            while not shutdown_requested.is_set():
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                await websocket.send_json(message)
        except WebSocketDisconnect:
            pass
        except Exception as error:  # the client went away while we were sending
            logger.debug("WebSocket closed: {}", error)
        finally:
            broadcaster.unsubscribe(queue)

    return app


app = create_app()
