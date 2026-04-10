from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
import shutil

from .config import Settings
from .storage import list_jobs, load_batch, load_job


@dataclass(slots=True)
class RetryCandidate:
    job_id: str
    original_filename: str
    retry_path: str
    account: str
    account_strategy: str
    delete_remote: bool


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def cleanup_jobs(
    settings: Settings,
    *,
    older_than_hours: float,
    statuses: set[str],
    dry_run: bool = True,
) -> dict:
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    removed_jobs: list[str] = []
    kept_jobs: list[str] = []

    for payload in list_jobs(settings.jobs_dir):
        status = payload.get("status")
        if status not in statuses:
            kept_jobs.append(payload["job_id"])
            continue

        timestamp = (
            _parse_iso_datetime(payload.get("completed_at"))
            or _parse_iso_datetime(payload.get("updated_at"))
            or _parse_iso_datetime(payload.get("created_at"))
        )
        if timestamp is None or timestamp > cutoff:
            kept_jobs.append(payload["job_id"])
            continue

        removed_jobs.append(payload["job_id"])
        if dry_run:
            continue

        job_path = settings.jobs_dir / payload["job_id"]
        if job_path.exists():
            shutil.rmtree(job_path)

    removed_batches: list[str] = []
    for batch_file in sorted(settings.runtime_dir.glob("batch_*.json")):
        payload = json.loads(batch_file.read_text(encoding="utf-8"))
        job_ids = [item["job_id"] for item in payload.get("items", [])]
        if job_ids and all(not (settings.jobs_dir / job_id).exists() for job_id in job_ids):
            removed_batches.append(payload["batch_id"])
            if not dry_run:
                batch_file.unlink(missing_ok=True)

    return {
        "dry_run": dry_run,
        "older_than_hours": older_than_hours,
        "statuses": sorted(statuses),
        "removed_jobs": removed_jobs,
        "removed_batches": removed_batches,
        "kept_jobs_count": len(kept_jobs),
    }


def collect_failed_retry_candidates(settings: Settings, batch_id: str) -> list[RetryCandidate]:
    batch_payload = load_batch(settings.runtime_dir, batch_id)
    candidates: list[RetryCandidate] = []

    for item in batch_payload.get("items", []):
        payload = load_job(settings.jobs_dir, item["job_id"])
        if payload.get("status") != "failed":
            continue
        meta = payload.get("meta", {})
        retry_path = meta.get("source_path") or meta.get("input_file")
        if not retry_path:
            continue
        path = Path(retry_path)
        if not path.exists():
            continue
        candidates.append(
            RetryCandidate(
                job_id=payload["job_id"],
                original_filename=payload["original_filename"],
                retry_path=str(path),
                account=payload.get("account_id") or "",
                account_strategy=meta.get("account_strategy") or "round-robin",
                delete_remote=bool(meta.get("delete_remote", settings.delete_remote)),
            )
        )

    return candidates
