from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

from .config import Settings
from .storage import load_batch, load_job, utc_now


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    started_at = _parse_iso_datetime(start)
    finished_at = _parse_iso_datetime(end)
    if started_at is None or finished_at is None:
        return None
    return max(0.0, (finished_at - started_at).total_seconds())


def _safe_round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def _size_mb(size_bytes: int | None) -> float | None:
    if size_bytes is None:
        return None
    return round(size_bytes / 1024 / 1024, 2)


def build_batch_report(settings: Settings, batch_id: str) -> dict:
    batch_payload = load_batch(settings.runtime_dir, batch_id)
    items: list[dict] = []
    status_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    source_mode_counts: Counter[str] = Counter()
    completed_durations: list[float] = []
    succeeded_durations: list[float] = []
    failed_durations: list[float] = []
    input_sizes: list[int] = []
    created_timestamps: list[datetime] = []
    completed_timestamps: list[datetime] = []

    for item in batch_payload.get("items", []):
        job_id = item["job_id"]
        try:
            payload = load_job(settings.jobs_dir, job_id)
        except FileNotFoundError:
            payload = {
                "job_id": job_id,
                "status": item.get("status", "queued"),
                "original_filename": item.get("original_filename"),
                "markdown_filename": item.get("markdown_filename"),
                "download_url": item.get("download_url"),
                "created_at": None,
                "updated_at": None,
                "completed_at": None,
                "output_file": None,
                "error": None,
                "meta": {},
            }

        status = payload.get("status", "queued")
        status_counts[status] += 1
        meta = payload.get("meta", {})
        source_mode = meta.get("source_mode") or "unknown"
        source_mode_counts[source_mode] += 1

        input_size_bytes = meta.get("file_size_bytes")
        if isinstance(input_size_bytes, int):
            input_sizes.append(input_size_bytes)

        created_at = payload.get("created_at")
        completed_at = payload.get("completed_at")
        updated_at = payload.get("updated_at")
        created_dt = _parse_iso_datetime(created_at)
        completed_dt = _parse_iso_datetime(completed_at or updated_at)
        if created_dt:
            created_timestamps.append(created_dt)
        if completed_dt and status in {"succeeded", "failed"}:
            completed_timestamps.append(completed_dt)

        elapsed_seconds = _duration_seconds(created_at, completed_at or updated_at)
        if elapsed_seconds is not None and status in {"succeeded", "failed"}:
            completed_durations.append(elapsed_seconds)
        if elapsed_seconds is not None and status == "succeeded":
            succeeded_durations.append(elapsed_seconds)
        if elapsed_seconds is not None and status == "failed":
            failed_durations.append(elapsed_seconds)

        error_code = (payload.get("error") or {}).get("code")
        if status == "failed" and error_code:
            error_counts[error_code] += 1

        items.append(
            {
                "job_id": job_id,
                "status": status,
                "original_filename": payload.get("original_filename"),
                "markdown_filename": payload.get("markdown_filename"),
                "download_url": payload.get("download_url"),
                "output_file": payload.get("output_file"),
                "created_at": created_at,
                "updated_at": updated_at,
                "completed_at": completed_at,
                "suggested_poll_after_seconds": payload.get("suggested_poll_after_seconds"),
                "input_size_bytes": input_size_bytes,
                "input_size_mb": _size_mb(input_size_bytes) if isinstance(input_size_bytes, int) else None,
                "elapsed_seconds": _safe_round(elapsed_seconds),
                "source_mode": meta.get("source_mode"),
                "source_path": meta.get("source_path"),
                "retry_count": int(meta.get("retry_count") or 0),
                "error_code": error_code,
                "error_message": (payload.get("error") or {}).get("message"),
            }
        )

    total = len(items)
    succeeded = status_counts.get("succeeded", 0)
    failed = status_counts.get("failed", 0)
    completed = succeeded + failed
    batch_wall_seconds = None
    if created_timestamps and completed_timestamps:
        batch_wall_seconds = _safe_round((max(completed_timestamps) - min(created_timestamps)).total_seconds())

    size_summary = {
        "total_input_size_bytes": sum(input_sizes),
        "total_input_size_mb": _safe_round(sum(input_sizes) / 1024 / 1024) if input_sizes else 0.0,
        "average_input_size_bytes": int(sum(input_sizes) / len(input_sizes)) if input_sizes else 0,
        "average_input_size_mb": _safe_round((sum(input_sizes) / len(input_sizes)) / 1024 / 1024) if input_sizes else 0.0,
    }

    timing_summary = {
        "batch_wall_seconds": batch_wall_seconds,
        "completed_jobs_average_seconds": _safe_round(sum(completed_durations) / len(completed_durations))
        if completed_durations
        else None,
        "completed_jobs_min_seconds": _safe_round(min(completed_durations)) if completed_durations else None,
        "completed_jobs_max_seconds": _safe_round(max(completed_durations)) if completed_durations else None,
        "succeeded_average_seconds": _safe_round(sum(succeeded_durations) / len(succeeded_durations))
        if succeeded_durations
        else None,
        "failed_average_seconds": _safe_round(sum(failed_durations) / len(failed_durations)) if failed_durations else None,
    }

    return {
        "batch_id": batch_payload["batch_id"],
        "created_at": batch_payload.get("created_at"),
        "generated_at": utc_now(),
        "format": batch_payload.get("format", "md"),
        "output_dir": str(settings.outputs_dir),
        "total": total,
        "counts": {
            "queued": status_counts.get("queued", 0),
            "running": status_counts.get("running", 0),
            "succeeded": succeeded,
            "failed": failed,
            "completed": completed,
        },
        "rates": {
            "success_rate_percent": _safe_round((succeeded / total) * 100) if total else 0.0,
            "failure_rate_percent": _safe_round((failed / total) * 100) if total else 0.0,
            "completion_rate_percent": _safe_round((completed / total) * 100) if total else 0.0,
        },
        "timing": timing_summary,
        "sizes": size_summary,
        "error_groups": dict(sorted(error_counts.items())),
        "source_mode_groups": dict(sorted(source_mode_counts.items())),
        "items": items,
    }


def render_batch_report_markdown(report: dict) -> str:
    timing = report["timing"]
    rates = report["rates"]
    sizes = report["sizes"]
    lines = [
        f"# Batch Report: `{report['batch_id']}`",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Created at: `{report.get('created_at')}`",
        f"- Output dir: `{report['output_dir']}`",
        f"- Total: `{report['total']}`",
        f"- Succeeded: `{report['counts']['succeeded']}`",
        f"- Running: `{report['counts']['running']}`",
        f"- Queued: `{report['counts']['queued']}`",
        f"- Failed: `{report['counts']['failed']}`",
        f"- Success rate: `{rates['success_rate_percent']}%`",
        f"- Completion rate: `{rates['completion_rate_percent']}%`",
        f"- Batch wall time: `{timing['batch_wall_seconds']}` seconds",
        f"- Avg completed job time: `{timing['completed_jobs_average_seconds']}` seconds",
        f"- Total input size: `{sizes['total_input_size_mb']}` MB",
        "",
        "## Item Status Table",
        "",
        "| Status | Original | Markdown | Size (MB) | Elapsed (s) | Retries | Job ID |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for item in report["items"]:
        lines.append(
            f"| {item['status']} | {item['original_filename']} | {item.get('markdown_filename') or ''} | "
            f"{item.get('input_size_mb') or ''} | {item.get('elapsed_seconds') or ''} | {item.get('retry_count') or 0} | {item['job_id']} |"
        )

    lines.extend(
        [
            "",
            "## Summary Metrics",
            "",
            f"- Failure rate: `{rates['failure_rate_percent']}%`",
            f"- Completed jobs min time: `{timing['completed_jobs_min_seconds']}` seconds",
            f"- Completed jobs max time: `{timing['completed_jobs_max_seconds']}` seconds",
            f"- Avg succeeded job time: `{timing['succeeded_average_seconds']}` seconds",
            f"- Avg failed job time: `{timing['failed_average_seconds']}` seconds",
            f"- Average input size: `{sizes['average_input_size_mb']}` MB",
            "",
            "## Source Modes",
            "",
        ]
    )

    for source_mode, count in report["source_mode_groups"].items():
        lines.append(f"- `{source_mode}`: `{count}`")

    if report["error_groups"]:
        lines.extend(["", "## Failure Groups", ""])
        for error_code, count in report["error_groups"].items():
            lines.append(f"- `{error_code}`: `{count}`")

    failed_items = [item for item in report["items"] if item["status"] == "failed"]
    if failed_items:
        lines.extend(["", "## Failed Items", ""])
        for item in failed_items:
            lines.extend(
                [
                    f"### {item['original_filename']}",
                    "",
                    f"- Job ID: `{item['job_id']}`",
                    f"- Error code: `{item.get('error_code') or ''}`",
                    f"- Error message: {item.get('error_message') or ''}",
                    f"- Retry count: `{item.get('retry_count') or 0}`",
                    "",
                ]
            )

    succeeded_items = [item for item in report["items"] if item["status"] == "succeeded"]
    if succeeded_items:
        lines.extend(["", "## Succeeded Outputs", ""])
        for item in succeeded_items:
            output_name = item.get("markdown_filename") or ""
            output_file = item.get("output_file") or ""
            lines.append(f"- `{output_name}` → `{output_file}`")

    return "\n".join(lines).rstrip() + "\n"


def report_output_path(settings: Settings, batch_id: str, suffix: str = "md") -> Path:
    reports_dir = settings.runtime_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir / f"{batch_id}.{suffix}"
