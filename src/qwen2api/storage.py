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


def sanitize_filename(filename: str) -> str:
    path = Path(filename or "upload.bin")
    stem = path.stem or "upload"
    suffix = path.suffix or ".bin"
    safe = []
    for ch in stem:
        if ch.isalnum() or ch in {"-", "_", " ", "."} or ("\u4e00" <= ch <= "\u9fff"):
            safe.append(ch)
        else:
            safe.append("_")
    normalized = "".join(safe).strip() or "upload"
    return f"{normalized}{suffix}"


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


def list_jobs(base_dir: Path) -> list[dict]:
    if not base_dir.exists():
        return []
    items: list[dict] = []
    for path in sorted(base_dir.glob("job_*/job.json"), reverse=True):
        try:
            items.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return items


def save_job(base_dir: Path, job_id: str, payload: dict) -> Path:
    path = job_file(base_dir, job_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def init_job_payload(
    job_id: str,
    *,
    original_filename: str,
    delete_remote: bool,
    account_id: str,
    account_strategy: str,
    queued: bool = False,
) -> dict:
    now = utc_now()
    status = "queued" if queued else "running"
    return {
        "job_id": job_id,
        "status": status,
        "format": "md",
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


def mark_job_running(payload: dict) -> dict:
    return {
        **payload,
        "status": "running",
        "updated_at": utc_now(),
    }


def _copy_upload_file(source_file, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_file.seek(0)
    with destination.open("wb") as target:
        shutil.copyfileobj(source_file, target)


async def persist_upload_file(upload: UploadFile, destination: Path) -> Path:
    await asyncio.to_thread(_copy_upload_file, upload.file, destination)
    return destination
