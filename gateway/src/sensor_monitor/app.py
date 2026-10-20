"""FastAPI application factory and REST/WebSocket transport layer."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import (
    APIRouter,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .hub import TelemetryHub
from .logging_setup import configure_logging
from .models import (
    Alert,
    DeviceHealth,
    FaultRequest,
    ServiceStats,
    Snapshot,
    TelemetryReading,
)
from .sources import SimulatorSource, TelemetrySource, build_source

LOGGER = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
# FastAPI caches endpoint metadata. Shared handlers must not capture an app's
# hub/source, otherwise those caches retain discarded application instances.
router = APIRouter()


@asynccontextmanager
async def lifespan(application: FastAPI):
    hub = application.state.hub
    task = asyncio.create_task(hub.run(), name="telemetry-acquisition")
    application.state.acquisition_task = task
    try:
        yield
    finally:
        await hub.stop()
        if task.done():
            if not task.cancelled() and task.exception() is not None:
                LOGGER.error("acquisition task had stopped before shutdown")
        else:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


@router.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    state = request.app.state
    task = getattr(state, "acquisition_task", None)
    running = task is not None and not task.done() and not state.hub.stopped
    return JSONResponse(
        status_code=200 if running else 503,
        content={
            "status": "ok" if running else "unavailable",
            "version": __version__,
            "source": state.source.name,
            "frames_accepted": state.hub.stats().frames_accepted,
            "acquisition_running": running,
        },
    )


@router.get("/api/v1/devices", response_model=list[DeviceHealth])
async def devices(request: Request) -> list[DeviceHealth]:
    return await request.app.state.hub.store.all_health()


@router.get("/api/v1/devices/{device_id}", response_model=DeviceHealth)
async def device(device_id: str, request: Request) -> DeviceHealth:
    health = await request.app.state.hub.store.health(device_id)
    if health is None:
        raise HTTPException(status_code=404, detail="device not found")
    return health


@router.get("/api/v1/devices/{device_id}/readings", response_model=list[TelemetryReading])
async def readings(
    device_id: str, request: Request, limit: int = Query(default=100, ge=1, le=600)
) -> list[TelemetryReading]:
    hub = request.app.state.hub
    if device_id not in await hub.store.device_ids():
        raise HTTPException(status_code=404, detail="device not found")
    return await hub.store.readings(device_id, limit)


@router.get("/api/v1/alerts", response_model=list[Alert])
async def alerts(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> list[Alert]:
    return await request.app.state.hub.store.alerts(limit)


@router.get("/api/v1/stats", response_model=ServiceStats)
async def stats(request: Request) -> ServiceStats:
    return request.app.state.hub.stats()


@router.get("/api/v1/snapshot", response_model=Snapshot)
async def snapshot(request: Request) -> Snapshot:
    return await request.app.state.hub.snapshot()


@router.post("/api/v1/simulator/fault")
async def simulator_fault(fault: FaultRequest, request: Request) -> dict[str, str]:
    source = request.app.state.source
    if not isinstance(source, SimulatorSource):
        raise HTTPException(
            status_code=409, detail="fault control is only available in simulator mode"
        )
    source.set_fault(fault.mode)
    LOGGER.info("simulator fault mode changed to %s", fault.mode)
    return {"mode": fault.mode.value}


@router.websocket("/ws/telemetry")
async def telemetry_socket(websocket: WebSocket) -> None:
    hub = websocket.app.state.hub
    await websocket.accept()
    try:
        async with hub.subscribe() as queue:
            # ASGI sends can yield; register before taking/sending the snapshot
            # so producer updates during socket startup remain queued.
            await websocket.send_json((await hub.snapshot()).model_dump(mode="json"))
            while True:
                from_queue = False
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    from_queue = True
                except TimeoutError:
                    event = {
                        "type": "heartbeat",
                        "stats": hub.stats().model_dump(mode="json"),
                        "devices": [
                            item.model_dump(mode="json")
                            for item in await hub.store.all_health()
                        ],
                    }
                try:
                    await websocket.send_json(event)
                finally:
                    if from_queue:
                        queue.task_done()
    except WebSocketDisconnect:
        return


def create_app(
    settings: Settings | None = None, source: TelemetrySource | None = None
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings)
    resolved_source = source or build_source(resolved_settings)
    app = FastAPI(
        title="STM32 Networked Sensor System",
        summary="STM32 telemetry gateway with REST, WebSocket, alerts, and health state",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.source = resolved_source
    app.state.hub = TelemetryHub(resolved_settings, resolved_source)
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    app.include_router(router)
    return app


app = create_app()
