from __future__ import annotations

from pathlib import Path
import mimetypes

from .config import Settings
from .qwen_adapter import NativeFlowResult, transcribe_via_qwen
from .storage import save_job, utc_now


async def run_transcription(
    *,
    settings: Settings,
    job_payload: dict,
    job_dir: Path,
    input_path: Path,
    export_format: str,
    delete_remote: bool,
    account_id: str,
    account_strategy: str,
) -> dict:
    try:
        flow_result = await transcribe_via_qwen(
            settings=settings,
            input_path=input_path,
            output_dir=job_dir / "outputs",
            export_format=export_format,
            delete_remote=delete_remote,
            account_id=account_id,
            account_strategy=account_strategy,
        )
        payload = build_success_payload(job_payload, flow_result)
    except Exception as error:  # noqa: BLE001
        payload = build_error_payload(job_payload, error)
        (job_dir / "error.txt").write_text(f"{type(error).__name__}: {error}\n", encoding="utf-8")
    save_job(settings.jobs_dir, job_payload["job_id"], payload)
    return payload


def build_success_payload(job_payload: dict, flow_result: NativeFlowResult) -> dict:
    output_file = flow_result.export_path.resolve()
    text = None
    if output_file.suffix.lower() in {".md", ".txt", ".json"}:
        text = output_file.read_text(encoding="utf-8")
    content_type = mimetypes.guess_type(output_file.name)[0]
    if output_file.suffix.lower() == ".md":
        content_type = "text/markdown"
    now = utc_now()
    return {
        **job_payload,
        "status": "succeeded",
        "text": text,
        "content_type": content_type,
        "output_file": str(output_file),
        "download_url": f"/api/v1/jobs/{job_payload['job_id']}/file",
        "record_id": flow_result.record_id,
        "gen_record_id": flow_result.gen_record_id,
        "remote_deleted": flow_result.remote_deleted,
        "account_id": flow_result.account_id or job_payload.get("account_id"),
        "account_label": flow_result.account_label or None,
        "updated_at": now,
        "completed_at": now,
        "meta": {
            **job_payload.get("meta", {}),
            "output_suffix": output_file.suffix.lower(),
        },
    }


def build_error_payload(job_payload: dict, error: Exception) -> dict:
    now = utc_now()
    return {
        **job_payload,
        "status": "failed",
        "updated_at": now,
        "completed_at": now,
        "error": {
            "message": str(error),
            "code": classify_error(error),
        },
    }


def classify_error(error: Exception) -> str:
    message = str(error).lower()
    if "unsupported export format" in message:
        return "UNSUPPORTED_FORMAT"
    if "timed out" in message:
        return "TRANSCRIPTION_TIMEOUT"
    if "api request failed: 401" in message or "api request failed: 403" in message:
        return "AUTH_EXPIRED"
    if "api request failed: 429" in message:
        return "RATE_LIMITED"
    return "TRANSCRIPTION_FAILED"
