"""FastAPI app: REST API, MJPEG live stream and WebSocket status push.

Start it with ``python -m app`` from the backend folder (see app/__main__.py);
the API docs are then at http://<API_HOST>:<API_PORT>/docs.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from loguru import logger

from app.broadcaster import Broadcaster
from app.config_store import ConfigStore
from app.db import Database
from app.pipeline import ConfigError, Detector, Pipeline
from app.schemas import EventOut, HealthOut, TableConfig, TablesUpdate, TableStatusOut
from app.settings import Settings, get_settings

# Set when the server is asked to stop (Ctrl+C), so endless MJPEG streams and
# WebSocket loops end and the shutdown does not hang on open browser tabs.
shutdown_requested = threading.Event()

MJPEG_BOUNDARY = "frame"
DB_FILENAME = "app.db"


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


def create_app(
    settings: Settings | None = None,
    detector_factory: Callable[[Settings], Detector] | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own settings and a fake detector."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = ConfigStore(settings.configs_dir)
        db = Database(settings.data_dir / DB_FILENAME)
        broadcaster = Broadcaster()
        extra = {"detector_factory": detector_factory} if detector_factory else {}
        pipeline = Pipeline(settings, store, db, broadcaster, **extra)
        app.state.pipeline, app.state.db, app.state.broadcaster = pipeline, db, broadcaster
        shutdown_requested.clear()
        pipeline.start()
        logger.info("API ready at http://{}:{}/docs", settings.api_host, settings.api_port)
        try:
            yield
        finally:
            shutdown_requested.set()
            pipeline.stop()
            db.close()

    app = FastAPI(
        title="Restaurant Table Occupancy",
        description="Live table occupancy from a CCTV stream: MJPEG video, table status, events and config.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def pipeline_of(request: Request) -> Pipeline:
        return request.app.state.pipeline

    @app.get("/api/health", response_model=HealthOut, tags=["system"])
    def health(request: Request) -> HealthOut:
        """Pipeline, model, source and MediaMTX state, and the processing speed."""
        return pipeline_of(request).health()

    @app.get("/api/stream", tags=["live"], response_class=StreamingResponse,
             responses={200: {"content": {f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}": {}}}})
    async def stream(request: Request) -> StreamingResponse:
        """Annotated live video as MJPEG. Use it directly: `<img src="/api/stream">`."""
        return StreamingResponse(
            mjpeg_parts(pipeline_of(request)),
            media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/snapshot", tags=["live"], response_class=Response,
             responses={200: {"content": {"image/jpeg": {}}}, 503: {"description": "No frame yet"}})
    def snapshot(request: Request) -> Response:
        """One raw (not annotated) frame as JPEG, e.g. as the background of the table editor."""
        jpeg = pipeline_of(request).snapshot_jpeg()
        if jpeg is None:
            raise HTTPException(503, "No frame has been received from the camera yet.")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/tables", response_model=list[TableStatusOut], tags=["live"])
    def tables(request: Request) -> list[TableStatusOut]:
        """Current status of every table."""
        return pipeline_of(request).status().tables

    @app.get("/api/events", response_model=list[EventOut], tags=["live"])
    def events(
        request: Request,
        limit: int = Query(50, ge=1, le=1000),
        video_id: str | None = Query(None, description="only events of this video"),
    ) -> list[dict]:
        """Recent table status changes, newest first."""
        return request.app.state.db.recent_events(limit, video_id)

    @app.get("/api/config", response_model=TableConfig, tags=["config"],
             responses={404: {"description": "The active video has no table config yet"}})
    def config(request: Request) -> TableConfig:
        """Table config of the active video."""
        pipeline = pipeline_of(request)
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
            return pipeline_of(request).update_tables(update)
        except ConfigError as error:
            raise HTTPException(422, str(error)) from error

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
