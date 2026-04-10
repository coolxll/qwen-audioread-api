from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..config import Settings, get_settings
from ..job_queue import enqueue_job
from ..schemas import (
    BatchJobItem,
    BatchTranscriptionResponse,
    LocalBatchTranscriptionRequest,
    LocalTranscriptionRequest,
    TranscriptionListResponse,
    TranscriptionResult,
)
from ..service import run_transcription
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
    reserve_markdown_name,
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


def _validate_local_input_path(path_value: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    if not path.exists():
        raise HTTPException(
            status_code=400,
            detail={"message": f"Local file not found: {path}", "code": "LOCAL_FILE_NOT_FOUND"},
        )
    if not path.is_file():
        raise HTTPException(
            status_code=400,
            detail={"message": f"Local path is not a file: {path}", "code": "LOCAL_FILE_INVALID"},
        )
    return path


def _prepare_job_payload(
    *,
    settings: Settings,
    job_id: str,
    current_job_dir: Path,
    original_name: str,
    input_path: Path,
    delete_remote: bool,
    account: str,
    account_strategy: str,
    queued: bool,
    batch_id: str | None = None,
    stored_input_name: str | None = None,
    source_mode: str,
    delete_input_after_success: bool,
    source_path: str | None = None,
) -> tuple[dict, Path, Path]:
    file_size_bytes = input_path.stat().st_size
    planned_markdown_name = reserve_markdown_name(settings.runtime_dir, settings.outputs_dir, original_name)
    try:
        payload = init_job_payload(
            job_id,
            original_filename=original_name,
            delete_remote=delete_remote,
            account_id=account,
            account_strategy=account_strategy,
            queued=queued,
            batch_id=batch_id,
            suggested_poll_seconds=suggested_poll_after_seconds(file_size_bytes),
            target_markdown_name=planned_markdown_name,
        )
        payload["meta"]["input_file"] = str(input_path)
        payload["meta"]["job_dir"] = str(current_job_dir)
        payload["meta"]["file_size_bytes"] = file_size_bytes
        payload["meta"]["source_mode"] = source_mode
        payload["meta"]["delete_input_after_success"] = delete_input_after_success
        if stored_input_name:
            payload["meta"]["stored_input_name"] = stored_input_name
        if source_path:
            payload["meta"]["source_path"] = source_path
        save_job(settings.jobs_dir, job_id, payload)
        return payload, current_job_dir, input_path
    except Exception:
        from ..storage import release_markdown_name_reservation

        release_markdown_name_reservation(settings.runtime_dir, planned_markdown_name)
        raise


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
) -> tuple[dict, Path, Path]:
    original_name = Path(upload.filename or "upload.bin").name
    safe_name = sanitize_filename(original_name)
    job_id = generate_job_id()
    current_job_dir = job_dir(settings.jobs_dir, job_id)
    input_path = current_job_dir / safe_name
    await persist_upload_file(upload, input_path)
    return _prepare_job_payload(
        settings=settings,
        job_id=job_id,
        current_job_dir=current_job_dir,
        original_name=original_name,
        input_path=input_path,
        delete_remote=delete_remote,
        account=account,
        account_strategy=account_strategy,
        queued=queued,
        batch_id=batch_id,
        stored_input_name=safe_name,
        source_mode="upload",
        delete_input_after_success=not settings.keep_uploaded_input,
    )


def create_job_from_local_path(
    *,
    path_value: str,
    delete_remote: bool,
    account: str,
    account_strategy: str,
    settings: Settings,
    queued: bool,
    batch_id: str | None = None,
) -> tuple[dict, Path, Path]:
    input_path = _validate_local_input_path(path_value)
    job_id = generate_job_id()
    current_job_dir = job_dir(settings.jobs_dir, job_id)
    return _prepare_job_payload(
        settings=settings,
        job_id=job_id,
        current_job_dir=current_job_dir,
        original_name=input_path.name,
        input_path=input_path,
        delete_remote=delete_remote,
        account=account,
        account_strategy=account_strategy,
        queued=queued,
        batch_id=batch_id,
        source_mode="local_path",
        delete_input_after_success=False,
        source_path=str(input_path),
    )


def _build_batch_response_from_payloads(
    *,
    settings: Settings,
    batch_id: str,
    payloads: list[dict],
) -> BatchTranscriptionResponse:
    items = [_build_batch_item(payload) for payload in payloads]
    response_payload = _batch_response_payload(
        batch_id=batch_id,
        settings=settings,
        items=items,
        created_at=utc_now(),
    )
    save_batch(settings.runtime_dir, batch_id, response_payload)
    return BatchTranscriptionResponse(**response_payload)


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
    "/transcriptions/local",
    response_model=TranscriptionResult,
    dependencies=[Depends(require_api_key)],
)
async def create_transcription_from_local_path(
    request: LocalTranscriptionRequest,
    settings: Settings = Depends(get_settings),
) -> TranscriptionResult:
    normalize_md_format(request.format)
    should_delete_remote = settings.delete_remote if request.delete_remote is None else request.delete_remote
    payload, current_job_dir, input_path = create_job_from_local_path(
        path_value=request.path,
        delete_remote=should_delete_remote,
        account=request.account,
        account_strategy=request.account_strategy,
        settings=settings,
        queued=False,
    )
    result = await run_transcription(
        settings=settings,
        job_payload=payload,
        job_dir=current_job_dir,
        input_path=input_path,
        delete_remote=should_delete_remote,
        account_id=request.account,
        account_strategy=request.account_strategy,
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
    request: Request,
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
    await enqueue_job(request.app, settings, payload)
    return TranscriptionResult(**payload)


@router.post(
    "/transcriptions/local/async",
    response_model=TranscriptionResult,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def create_transcription_local_async(
    request: LocalTranscriptionRequest,
    http_request: Request,
    settings: Settings = Depends(get_settings),
) -> TranscriptionResult:
    normalize_md_format(request.format)
    should_delete_remote = settings.delete_remote if request.delete_remote is None else request.delete_remote
    payload, current_job_dir, input_path = create_job_from_local_path(
        path_value=request.path,
        delete_remote=should_delete_remote,
        account=request.account,
        account_strategy=request.account_strategy,
        settings=settings,
        queued=True,
    )
    await enqueue_job(http_request.app, settings, payload)
    return TranscriptionResult(**payload)


@router.post(
    "/transcriptions/batch",
    response_model=BatchTranscriptionResponse,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def create_transcription_batch(
    request: Request,
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
    payloads: list[dict] = []

    for upload in files:
        payload, current_job_dir, input_path = await create_job_from_upload(
            upload=upload,
            delete_remote=should_delete_remote,
            account=account,
            account_strategy=account_strategy,
            settings=settings,
            queued=True,
            batch_id=batch_id,
        )
        await enqueue_job(request.app, settings, payload)
        payloads.append(payload)

    return _build_batch_response_from_payloads(
        settings=settings,
        batch_id=batch_id,
        payloads=payloads,
    )


@router.post(
    "/transcriptions/local/batch",
    response_model=BatchTranscriptionResponse,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def create_transcription_local_batch(
    request: LocalBatchTranscriptionRequest,
    http_request: Request,
    settings: Settings = Depends(get_settings),
) -> BatchTranscriptionResponse:
    normalize_md_format(request.format)
    if not request.paths:
        raise HTTPException(
            status_code=400,
            detail={"message": "paths must not be empty", "code": "EMPTY_PATHS"},
        )

    should_delete_remote = settings.delete_remote if request.delete_remote is None else request.delete_remote
    batch_id = generate_batch_id()
    payloads: list[dict] = []
    validated_paths = [str(_validate_local_input_path(path_value)) for path_value in request.paths]

    for path_value in validated_paths:
        payload, current_job_dir, input_path = create_job_from_local_path(
            path_value=path_value,
            delete_remote=should_delete_remote,
            account=request.account,
            account_strategy=request.account_strategy,
            settings=settings,
            queued=True,
            batch_id=batch_id,
        )
        await enqueue_job(http_request.app, settings, payload)
        payloads.append(payload)

    return _build_batch_response_from_payloads(
        settings=settings,
        batch_id=batch_id,
        payloads=payloads,
    )


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
