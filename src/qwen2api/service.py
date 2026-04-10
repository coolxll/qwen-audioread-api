from __future__ import annotations

from pathlib import Path
import mimetypes

from .config import Settings
from .qwen_adapter import NativeFlowResult, transcribe_via_qwen
from .storage import mark_job_running, next_available_markdown_name, save_job, utc_now


def move_output_to_flat_dir(
    *,
    settings: Settings,
    flow_result: NativeFlowResult,
    target_markdown_name: str,
    original_filename: str,
) -> Path:
    source = flow_result.export_path.resolve()
    target = settings.outputs_dir / target_markdown_name
    if target.exists() and target != source:
        target = settings.outputs_dir / next_available_markdown_name(
            settings.outputs_dir,
            original_filename,
        )
    if target != source:
        source.replace(target)
        sidecar = source.with_suffix(source.suffix + ".meta.json")
        if sidecar.exists():
            sidecar.replace(target.with_suffix(target.suffix + ".meta.json"))
    return target


async def run_transcription(
    *,
    settings: Settings,
    job_payload: dict,
    job_dir: Path,
    input_path: Path,
    delete_remote: bool,
    account_id: str,
    account_strategy: str,
) -> dict:
    running_payload = mark_job_running(job_payload)
    save_job(settings.jobs_dir, job_payload["job_id"], running_payload)
    try:
        flow_result = await transcribe_via_qwen(
            settings=settings,
            input_path=input_path,
            output_dir=job_dir / "outputs",
            export_format="md",
            delete_remote=delete_remote,
            account_id=account_id,
            account_strategy=account_strategy,
        )
        payload = build_success_payload(settings=settings, job_payload=running_payload, flow_result=flow_result)
    except Exception as error:  # noqa: BLE001
        payload = build_error_payload(running_payload, error)
        (job_dir / "error.txt").write_text(f"{type(error).__name__}: {error}\n", encoding="utf-8")
    save_job(settings.jobs_dir, running_payload["job_id"], payload)
    return payload


async def run_transcription_background(
    *,
    settings: Settings,
    job_payload: dict,
    job_dir: Path,
    input_path: Path,
    delete_remote: bool,
    account_id: str,
    account_strategy: str,
) -> None:
    await run_transcription(
        settings=settings,
        job_payload=job_payload,
        job_dir=job_dir,
        input_path=input_path,
        delete_remote=delete_remote,
        account_id=account_id,
        account_strategy=account_strategy,
    )


def build_success_payload(*, settings: Settings, job_payload: dict, flow_result: NativeFlowResult) -> dict:
    target_markdown_name = str(
        job_payload.get("meta", {}).get("target_markdown_name")
        or f"{Path(job_payload['original_filename']).stem}.md"
    )
    output_file = move_output_to_flat_dir(
        settings=settings,
        flow_result=flow_result,
        target_markdown_name=target_markdown_name,
        original_filename=job_payload["original_filename"],
    )
    final_markdown_name = output_file.name
    text = output_file.read_text(encoding="utf-8") if output_file.suffix.lower() == ".md" else None
    content_type = mimetypes.guess_type(output_file.name)[0] or "text/markdown"
    if output_file.suffix.lower() == ".md":
        content_type = "text/markdown"
    now = utc_now()
    return {
        **job_payload,
        "status": "succeeded",
        "format": "md",
        "markdown_filename": final_markdown_name,
        "suggested_poll_after_seconds": job_payload.get("suggested_poll_after_seconds")
        or job_payload.get("meta", {}).get("suggested_poll_after_seconds"),
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
            "target_markdown_name": final_markdown_name,
            "output_suffix": output_file.suffix.lower(),
            "flat_output": True,
        },
    }


def build_error_payload(job_payload: dict, error: Exception) -> dict:
    now = utc_now()
    return {
        **job_payload,
        "status": "failed",
        "format": "md",
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
