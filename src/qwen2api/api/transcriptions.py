from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import Settings, get_settings
from ..schemas import BatchJobItem, BatchTranscriptionResponse, TranscriptionListResponse, TranscriptionResult
from ..service import run_transcription, run_transcription_background
from ..storage import (
    generate_batch_id,
    generate_job_id,
    init_job_payload,
    job_dir,
    list_jobs,
    load_batch,
    load_job,
    markdown_filename,
    persist_upload_file,
    sanitize_filename,
    save_batch,
    save_job,
    suggested_poll_after_seconds,
    utc_now,
)

router = APIRouter(prefix="/api/v1", tags=["transcriptions"])


def _job_url(job_id: str) -> str:
    return f"/api/v1/jobs/{job_id}"


def _download_url(job_id: str) -> str:
    return f"/api/v1/jobs/{job_id}/file"


def _planned_markdown_name(
    settings: Settings,
    original_filename: str,
    reserved_markdown_names: set[str] | None = None,
) -> str:
    base_name = markdown_filename(original_filename)
    candidate_name = base_name
    reserved = reserved_markdown_names if reserved_markdown_names is not None else set()
    stem = Path(base_name).stem
    suffix = Path(base_name).suffix
    index = 2
    while (settings.outputs_dir / candidate_name).exists() or candidate_name in reserved:
        candidate_name = f"{stem}-{index}{suffix}"
        index += 1
    if reserved_markdown_names is not None:
        reserved_markdown_names.add(candidate_name)
    return candidate_name


def _suggested_poll_seconds(payload: dict) -> int:
    return int(
        payload.get("suggested_poll_after_seconds")
        or payload.get("meta", {}).get("suggested_poll_after_seconds")
        or 60
    )


def _markdown_name_from_payload(payload: dict) -> str:
    return str(
        payload.get("markdown_filename")
        or payload.get("meta", {}).get("target_markdown_name")
        or markdown_filename(payload["original_filename"])
    )


def _build_batch_item(payload: dict) -> BatchJobItem:
    job_id = payload["job_id"]
    return BatchJobItem(
        job_id=job_id,
        original_filename=payload["original_filename"],
        markdown_filename=_markdown_name_from_payload(payload),
        status=payload["status"],
        job_url=_job_url(job_id),
        download_url=payload.get("download_url") or _download_url(job_id),
        suggested_poll_after_seconds=_suggested_poll_seconds(payload),
    )


def _batch_response_payload(
    *,
    batch_id: str,
    settings: Settings,
    items: list[BatchJobItem],
    created_at: str | None = None,
) -> dict:
    payload = {
        "batch_id": batch_id,
        "total": len(items),
        "accepted": len(items),
        "format": "md",
        "output_dir": str(settings.outputs_dir),
        "items": [item.model_dump() for item in items],
    }
    if created_at:
        payload["created_at"] = created_at
    return payload


def _refresh_batch_payload(settings: Settings, payload: dict) -> dict:
    refreshed_items: list[BatchJobItem] = []
    for item in payload.get("items", []):
        job_id = item["job_id"]
        try:
            job_payload = load_job(settings.jobs_dir, job_id)
            refreshed_items.append(_build_batch_item(job_payload))
        except FileNotFoundError:
            refreshed_items.append(BatchJobItem(**item))

    return {
        **payload,
        "output_dir": str(settings.outputs_dir),
        "items": [item.model_dump() for item in refreshed_items],
        "total": len(refreshed_items),
        "accepted": len(refreshed_items),
    }


async def require_api_key(
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    expected = settings.api_key.strip()
    if not expected:
        return
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    provided = x_api_key or bearer
    if provided != expected:
        raise HTTPException(status_code=401, detail={"message": "Invalid API key", "code": "UNAUTHORIZED"})


def normalize_md_format(format_value: str | None) -> str:
    export_format = (format_value or "md").strip().lower()
    if export_format not in {"", "md", "markdown"}:
        raise HTTPException(
            status_code=400,
            detail={"message": "Only md output is supported", "code": "MD_ONLY_OUTPUT"},
        )
    return "md"


async def create_job_from_upload(
    *,
    upload: UploadFile,
    delete_remote: bool,
    account: str,
    account_strategy: str,
    settings: Settings,
    queued: bool,
    batch_id: str | None = None,
    reserved_markdown_names: set[str] | None = None,
) -> tuple[dict, Path, Path]:
    job_id = generate_job_id()
    current_job_dir = job_dir(settings.jobs_dir, job_id)
    original_name = Path(upload.filename or "upload.bin").name
    safe_name = sanitize_filename(original_name)
    input_path = current_job_dir / safe_name
    await persist_upload_file(upload, input_path)

    file_size_bytes = input_path.stat().st_size
    suggested_seconds = suggested_poll_after_seconds(file_size_bytes)
    planned_markdown_name = _planned_markdown_name(
        settings,
        original_name,
        reserved_markdown_names=reserved_markdown_names,
    )

    payload = init_job_payload(
        job_id,
        original_filename=original_name,
        delete_remote=delete_remote,
        account_id=account,
        account_strategy=account_strategy,
        queued=queued,
        batch_id=batch_id,
        suggested_poll_seconds=suggested_seconds,
        target_markdown_name=planned_markdown_name,
    )
    payload["meta"]["input_file"] = str(input_path)
    payload["meta"]["job_dir"] = str(current_job_dir)
    payload["meta"]["stored_input_name"] = safe_name
    payload["meta"]["file_size_bytes"] = file_size_bytes
    payload["meta"]["created_by_api"] = True
    save_job(settings.jobs_dir, job_id, payload)
    return payload, current_job_dir, input_path


@router.post("/transcriptions", response_model=TranscriptionResult, dependencies=[Depends(require_api_key)])
async def create_transcription(
    file: UploadFile = File(...),
    format: str | None = Form(default=None),
    delete_remote: bool | None = Form(default=None),
    account: str = Form(default=""),
    account_strategy: str = Form(default="round-robin"),
    settings: Settings = Depends(get_settings),
) -> TranscriptionResult:
    normalize_md_format(format)
    should_delete_remote = settings.delete_remote if delete_remote is None else delete_remote
    payload, current_job_dir, input_path = await create_job_from_upload(
        upload=file,
        delete_remote=should_delete_remote,
        account=account,
        account_strategy=account_strategy,
        settings=settings,
        queued=False,
    )
    result = await run_transcription(
        settings=settings,
        job_payload=payload,
        job_dir=current_job_dir,
        input_path=input_path,
        delete_remote=should_delete_remote,
        account_id=account,
        account_strategy=account_strategy,
    )
    if result["status"] == "failed":
        raise HTTPException(status_code=500, detail=result["error"])
    return TranscriptionResult(**result)


@router.post(
    "/transcriptions/async",
    response_model=TranscriptionResult,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def create_transcription_async(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    format: str | None = Form(default=None),
    delete_remote: bool | None = Form(default=None),
    account: str = Form(default=""),
    account_strategy: str = Form(default="round-robin"),
    settings: Settings = Depends(get_settings),
) -> TranscriptionResult:
    normalize_md_format(format)
    should_delete_remote = settings.delete_remote if delete_remote is None else delete_remote
    payload, current_job_dir, input_path = await create_job_from_upload(
        upload=file,
        delete_remote=should_delete_remote,
        account=account,
        account_strategy=account_strategy,
        settings=settings,
        queued=True,
    )
    background_tasks.add_task(
        run_transcription_background,
        settings=settings,
        job_payload=payload,
        job_dir=current_job_dir,
        input_path=input_path,
        delete_remote=should_delete_remote,
        account_id=account,
        account_strategy=account_strategy,
    )
    return TranscriptionResult(**payload)


@router.post(
    "/transcriptions/batch",
    response_model=BatchTranscriptionResponse,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def create_transcription_batch(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    format: str | None = Form(default=None),
    delete_remote: bool | None = Form(default=None),
    account: str = Form(default=""),
    account_strategy: str = Form(default="round-robin"),
    settings: Settings = Depends(get_settings),
) -> BatchTranscriptionResponse:
    normalize_md_format(format)
    should_delete_remote = settings.delete_remote if delete_remote is None else delete_remote
    batch_id = generate_batch_id()
    reserved_markdown_names: set[str] = set()
    items: list[BatchJobItem] = []

    for upload in files:
        payload, current_job_dir, input_path = await create_job_from_upload(
            upload=upload,
            delete_remote=should_delete_remote,
            account=account,
            account_strategy=account_strategy,
            settings=settings,
            queued=True,
            batch_id=batch_id,
            reserved_markdown_names=reserved_markdown_names,
        )
        background_tasks.add_task(
            run_transcription_background,
            settings=settings,
            job_payload=payload,
            job_dir=current_job_dir,
            input_path=input_path,
            delete_remote=should_delete_remote,
            account_id=account,
            account_strategy=account_strategy,
        )
        items.append(_build_batch_item(payload))

    response_payload = _batch_response_payload(
        batch_id=batch_id,
        settings=settings,
        items=items,
        created_at=utc_now(),
    )
    save_batch(settings.runtime_dir, batch_id, response_payload)
    return BatchTranscriptionResponse(**response_payload)


@router.get("/batches/{batch_id}", response_model=BatchTranscriptionResponse, dependencies=[Depends(require_api_key)])
async def get_batch(batch_id: str, settings: Settings = Depends(get_settings)) -> BatchTranscriptionResponse:
    try:
        payload = load_batch(settings.runtime_dir, batch_id)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail={"message": f"Batch not found: {batch_id}", "code": "BATCH_NOT_FOUND"},
        ) from error

    refreshed = _refresh_batch_payload(settings, payload)
    save_batch(settings.runtime_dir, batch_id, refreshed)
    return BatchTranscriptionResponse(**refreshed)


@router.get("/jobs", response_model=TranscriptionListResponse, dependencies=[Depends(require_api_key)])
async def get_jobs(settings: Settings = Depends(get_settings), limit: int = 50) -> TranscriptionListResponse:
    raw_items = list_jobs(settings.jobs_dir)
    sliced = raw_items[: max(1, min(limit, 200))]
    return TranscriptionListResponse(items=[TranscriptionResult(**item) for item in sliced], total=len(raw_items))


@router.get("/jobs/{job_id}", response_model=TranscriptionResult, dependencies=[Depends(require_api_key)])
async def get_job(job_id: str, settings: Settings = Depends(get_settings)) -> TranscriptionResult:
    try:
        payload = load_job(settings.jobs_dir, job_id)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail={"message": f"Job not found: {job_id}", "code": "JOB_NOT_FOUND"},
        ) from error
    return TranscriptionResult(**payload)


@router.get("/jobs/{job_id}/file", dependencies=[Depends(require_api_key)])
async def download_job_file(job_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    try:
        payload = load_job(settings.jobs_dir, job_id)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail={"message": f"Job not found: {job_id}", "code": "JOB_NOT_FOUND"},
        ) from error
    output_file = payload.get("output_file")
    if payload.get("status") != "succeeded" or not output_file:
        raise HTTPException(
            status_code=409,
            detail={"message": f"Job {job_id} has no downloadable output", "code": "JOB_NOT_READY"},
        )
    path = Path(output_file)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail={"message": f"Output file missing for {job_id}", "code": "OUTPUT_MISSING"},
        )
    return FileResponse(path=path, filename=path.name, media_type="text/markdown")
