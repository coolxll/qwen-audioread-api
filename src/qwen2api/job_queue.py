from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .config import Settings
from .service import run_transcription_background
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


async def _worker_loop(app: FastAPI, worker_index: int) -> None:
    queue: asyncio.Queue[dict[str, Any]] = app.state.job_queue
    while True:
        task = await queue.get()
        try:
            await run_transcription_background(**task)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # run_transcription_background already persists failure state; this is a final guard.
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
