"""FastAPI entrypoint: routes, WebSockets and the static dashboard."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__
from backend import call_db
from backend.config import FRONTEND_DIR, settings
from backend.routes import api_routes, plivo_routes, websocket_routes
from backend.services.warmup import warmup_manager
from backend.session_store import registry
from backend.utils.logging_config import setup_logging

setup_logging(settings.log_level, settings.log_format)
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info(
        "server_start",
        extra={
            "version": __version__,
            "host": settings.host,
            "port": settings.port,
            "providers": {
                "plivo": settings.plivo_configured,
                "assemblyai": settings.assemblyai_configured,
                "sarvam": settings.sarvam_configured,
                "inworld": settings.inworld_configured,
                "speechify": settings.speechify_configured,
            },
            "tts_provider": settings.tts_provider,
            "missing_credentials": settings.missing_credentials_for(settings.tts_provider),
            "public_base_url": settings.public_base_url or None,
        },
    )
    if settings.missing_credentials_for(settings.tts_provider):
        log.warning(
            "missing_credentials",
            extra={
                "missing": settings.missing_credentials_for(settings.tts_provider),
                "hint": "Copy .env.example to .env and fill in the keys.",
            },
        )

    call_db.init(settings.db_path)

    # Warm provider sessions in the background; never block startup.
    warmup_manager.start()

    yield

    await warmup_manager.aclose()
    for agent in list(registry.active.values()):
        try:
            await agent.end("server_shutdown")
        except Exception:
            log.exception("shutdown_agent_end_failed")
    log.info("server_stop")


app = FastAPI(
    title="Streaming Telephony Voice Agent",
    version=__version__,
    description="Plivo + AssemblyAI + Sarvam 105B + Inworld TTS 2 / Speechify Simba 3.5 real-time voice agent",
    lifespan=lifespan,
)

app.include_router(api_routes.router)
app.include_router(plivo_routes.router)
app.include_router(websocket_routes.router)

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
    )
