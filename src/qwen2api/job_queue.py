from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .config import Settings
from .service import run_transcription, serialize_job_payload
from .storage import list_jobs, save_job, utc_now


def build_job_task(settings: Settings, payload: dict) -> dict[str, Any] | None:
    meta = payload.get("meta", {})
    job_dir_value = meta.get("job_dir")
    input_file_value = meta.get("input_file")
    if not job_dir_value or not input_file_value:
        return None
    return {
        "settings": settings,
        "job_payload": payload,
        "job_dir": Path(job_dir_value),
        "input_path": Path(input_file_value),
        "delete_remote": bool(meta.get("delete_remote", settings.delete_remote)),
        "account_id": payload.get("account_id") or "",
        "account_strategy": meta.get("account_strategy") or "round-robin",
    }


async def enqueue_job(app: FastAPI, settings: Settings, payload: dict) -> None:
    task = build_job_task(settings, payload)
    if task is None:
        raise ValueError(f"Job payload is missing input metadata: {payload.get('job_id')}")
    await app.state.job_queue.put(task)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def retry_wait_seconds(payload: dict) -> float:
    retry_not_before = _parse_iso_datetime(payload.get("meta", {}).get("retry_not_before"))
    if retry_not_before is None:
        return 0.0
    return max(0.0, (retry_not_before - datetime.now(UTC)).total_seconds())


def should_retry_job(settings: Settings, payload: dict) -> bool:
    if payload.get("status") != "failed":
        return False
    error_code = (payload.get("error") or {}).get("code")
    if not error_code or error_code not in settings.retryable_error_codes:
        return False
    retry_count = int(payload.get("meta", {}).get("retry_count") or 0)
    return retry_count < settings.max_retries


def build_retry_payload(settings: Settings, payload: dict) -> dict:
    now = datetime.now(UTC)
    retry_count = int(payload.get("meta", {}).get("retry_count") or 0) + 1
    retry_not_before = now + timedelta(seconds=settings.retry_delay_seconds)
    return {
        **payload,
        "status": "queued",
        "updated_at": utc_now(),
        "completed_at": None,
        "error": None,
        "meta": {
            **payload.get("meta", {}),
            "retry_count": retry_count,
            "max_retries": settings.max_retries,
            "retry_not_before": retry_not_before.isoformat(timespec="seconds"),
            "last_retry_at": now.isoformat(timespec="seconds"),
        },
    }


async def _worker_loop(app: FastAPI, worker_index: int) -> None:
    queue: asyncio.Queue[dict[str, Any]] = app.state.job_queue
    while True:
        task = await queue.get()
        try:
            wait_seconds = retry_wait_seconds(task["job_payload"])
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            payload = await run_transcription(**task)
            if should_retry_job(task["settings"], payload):
                retry_payload = build_retry_payload(task["settings"], payload)
                save_job(
                    task["settings"].jobs_dir,
                    retry_payload["job_id"],
                    serialize_job_payload(task["settings"], retry_payload),
                )
                task["job_payload"] = retry_payload
                await queue.put(task)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # run_transcription already persists failure state; this is a final guard.
            pass
        finally:
            queue.task_done()


async def start_job_workers(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    app.state.job_queue = asyncio.Queue()
    app.state.job_workers = [
        asyncio.create_task(_worker_loop(app, idx), name=f"qwen2api-job-worker-{idx}")
        for idx in range(settings.job_worker_count)
    ]
    await recover_pending_jobs(app)


async def stop_job_workers(app: FastAPI) -> None:
    workers: list[asyncio.Task[Any]] = getattr(app.state, "job_workers", [])
    for worker in workers:
        worker.cancel()
    for worker in workers:
        with suppress(asyncio.CancelledError):
            await worker


async def recover_pending_jobs(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    pending = list(reversed(list_jobs(settings.jobs_dir)))
    for payload in pending:
        status = payload.get("status")
        if status not in {"queued", "running"}:
            continue

        task = build_job_task(settings, payload)
        if task is None:
            continue

        if status == "running":
            payload = {
                **payload,
                "status": "queued",
                "updated_at": utc_now(),
                "meta": {
                    **payload.get("meta", {}),
                    "requeued_after_restart": True,
                },
            }
            save_job(settings.jobs_dir, payload["job_id"], payload)
            task["job_payload"] = payload

        await app.state.job_queue.put(task)
