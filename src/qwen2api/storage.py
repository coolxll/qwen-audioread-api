from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import json
import shutil
import uuid

from fastapi import UploadFile


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def generate_job_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"job_{stamp}_{uuid.uuid4().hex[:8]}"


def job_dir(base_dir: Path, job_id: str) -> Path:
    path = (base_dir / job_id).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_file(base_dir: Path, job_id: str) -> Path:
    return job_dir(base_dir, job_id) / "job.json"


def load_job(base_dir: Path, job_id: str) -> dict:
    path = job_file(base_dir, job_id)
    if not path.exists():
        raise FileNotFoundError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def save_job(base_dir: Path, job_id: str, payload: dict) -> Path:
    path = job_file(base_dir, job_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def init_job_payload(
    job_id: str,
    *,
    original_filename: str,
    export_format: str,
    delete_remote: bool,
    account_id: str,
    account_strategy: str,
) -> dict:
    now = utc_now()
    return {
        "job_id": job_id,
        "status": "running",
        "format": export_format,
        "content_type": None,
        "text": None,
        "output_file": None,
        "download_url": None,
        "record_id": None,
        "gen_record_id": None,
        "remote_deleted": None,
        "account_id": account_id or None,
        "account_label": None,
        "original_filename": original_filename,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "error": None,
        "meta": {
            "delete_remote": delete_remote,
            "account_strategy": account_strategy,
        },
    }


def _copy_upload_file(source_file, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_file.seek(0)
    with destination.open("wb") as target:
        shutil.copyfileobj(source_file, target)


async def persist_upload_file(upload: UploadFile, destination: Path) -> Path:
    await asyncio.to_thread(_copy_upload_file, upload.file, destination)
    return destination
