from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import Settings, get_settings
from ..schemas import BatchJobItem, BatchTranscriptionResponse, TranscriptionListResponse, TranscriptionResult
from ..service import run_transcription, run_transcription_background
from ..storage import (
    generate_job_id,
    init_job_payload,
    job_dir,
    list_jobs,
    load_job,
    persist_upload_file,
    sanitize_filename,
    save_job,
)

router = APIRouter(prefix="/api/v1", tags=["transcriptions"])


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
) -> tuple[dict, Path, Path]:
    job_id = generate_job_id()
    current_job_dir = job_dir(settings.jobs_dir, job_id)
    original_name = Path(upload.filename or "upload.bin").name
    safe_name = sanitize_filename(original_name)
    input_path = current_job_dir / safe_name
    await persist_upload_file(upload, input_path)

    payload = init_job_payload(
        job_id,
        original_filename=original_name,
        delete_remote=delete_remote,
        account_id=account,
        account_strategy=account_strategy,
        queued=queued,
    )
    payload["meta"]["input_file"] = str(input_path)
    payload["meta"]["job_dir"] = str(current_job_dir)
    payload["meta"]["stored_input_name"] = safe_name
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


@router.post("/transcriptions/async", response_model=TranscriptionResult, status_code=202, dependencies=[Depends(require_api_key)])
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


@router.post("/transcriptions/batch", response_model=BatchTranscriptionResponse, status_code=202, dependencies=[Depends(require_api_key)])
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
    items: list[BatchJobItem] = []
    for upload in files:
        payload, current_job_dir, input_path = await create_job_from_upload(
            upload=upload,
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
        items.append(
            BatchJobItem(
                job_id=payload["job_id"],
                original_filename=payload["original_filename"],
                status=payload["status"],
                job_url=f"/api/v1/jobs/{payload['job_id']}",
                download_url=None,
            )
        )
    return BatchTranscriptionResponse(total=len(items), accepted=len(items), format="md", items=items)


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
        raise HTTPException(status_code=404, detail={"message": f"Job not found: {job_id}", "code": "JOB_NOT_FOUND"}) from error
    return TranscriptionResult(**payload)


@router.get("/jobs/{job_id}/file", dependencies=[Depends(require_api_key)])
async def download_job_file(job_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    try:
        payload = load_job(settings.jobs_dir, job_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"message": f"Job not found: {job_id}", "code": "JOB_NOT_FOUND"}) from error
    output_file = payload.get("output_file")
    if payload.get("status") != "succeeded" or not output_file:
        raise HTTPException(status_code=409, detail={"message": f"Job {job_id} has no downloadable output", "code": "JOB_NOT_READY"})
    path = Path(output_file)
    if not path.exists():
        raise HTTPException(status_code=404, detail={"message": f"Output file missing for {job_id}", "code": "OUTPUT_MISSING"})
    return FileResponse(path=path, filename=path.name, media_type="text/markdown")
