from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import health_router, transcriptions_router
from .config import Settings, get_settings
from .job_queue import start_job_workers, stop_job_workers


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = resolved_settings
        await start_job_workers(app)
        try:
            yield
        finally:
            await stop_job_workers(app)

    app = FastAPI(title="qwen2api", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.include_router(health_router)
    app.include_router(transcriptions_router)
    return app


app = create_app()
