from __future__ import annotations

from collections import Counter
from pathlib import Path

from .config import Settings
from .storage import load_batch, load_job, utc_now


def build_batch_report(settings: Settings, batch_id: str) -> dict:
    batch_payload = load_batch(settings.runtime_dir, batch_id)
    items: list[dict] = []
    counter: Counter[str] = Counter()

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
        counter[status] += 1
        meta = payload.get("meta", {})
        items.append(
            {
                "job_id": job_id,
                "status": status,
                "original_filename": payload.get("original_filename"),
                "markdown_filename": payload.get("markdown_filename"),
                "download_url": payload.get("download_url"),
                "output_file": payload.get("output_file"),
                "created_at": payload.get("created_at"),
                "updated_at": payload.get("updated_at"),
                "completed_at": payload.get("completed_at"),
                "suggested_poll_after_seconds": payload.get("suggested_poll_after_seconds"),
                "source_mode": meta.get("source_mode"),
                "source_path": meta.get("source_path"),
                "error_code": (payload.get("error") or {}).get("code"),
                "error_message": (payload.get("error") or {}).get("message"),
            }
        )

    return {
        "batch_id": batch_payload["batch_id"],
        "created_at": batch_payload.get("created_at"),
        "generated_at": utc_now(),
        "format": batch_payload.get("format", "md"),
        "output_dir": str(settings.outputs_dir),
        "total": len(items),
        "counts": {
            "queued": counter.get("queued", 0),
            "running": counter.get("running", 0),
            "succeeded": counter.get("succeeded", 0),
            "failed": counter.get("failed", 0),
        },
        "items": items,
    }


def render_batch_report_markdown(report: dict) -> str:
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
        "",
        "## Items",
        "",
        "| Status | Original | Markdown | Job ID |",
        "| --- | --- | --- | --- |",
    ]

    for item in report["items"]:
        lines.append(
            f"| {item['status']} | {item['original_filename']} | {item.get('markdown_filename') or ''} | {item['job_id']} |"
        )

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
