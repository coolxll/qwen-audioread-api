from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from qwen_http_runtime.minimal_auth import convert_legacy_to_minimal, is_minimal_auth_format, load_auth_file

from ..config import Settings, get_settings
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready")
async def ready(settings: Settings = Depends(get_settings)) -> JSONResponse:
    try:
        payload = load_auth_file(settings.qwen_auth_state_path)
    except FileNotFoundError:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "auth_missing"})
    except ValueError:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "auth_invalid"})

    minimal = payload if is_minimal_auth_format(payload) else convert_legacy_to_minimal(payload)
    if not minimal or not str(minimal.get("tongyi_sso_ticket") or "").strip():
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "ticket_missing"})
    return JSONResponse(status_code=200, content={"status": "ready"})
