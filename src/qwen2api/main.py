from __future__ import annotations

from fastapi import FastAPI

from .api import health_router, transcriptions_router
from .config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="qwen2api", version="0.1.0")
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(transcriptions_router)
    return app


app = create_app()
