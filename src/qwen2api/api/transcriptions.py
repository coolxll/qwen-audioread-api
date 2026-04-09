from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import Settings, get_settings
from ..schemas import TranscriptionResult
from ..service import run_transcription
from ..storage import generate_job_id, init_job_payload, job_dir, load_job, persist_upload_file, save_job

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


@router.post("/transcriptions", response_model=TranscriptionResult, dependencies=[Depends(require_api_key)])
async def create_transcription(
    file: UploadFile = File(...),
    format: str | None = Form(default=None),
    delete_remote: bool | None = Form(default=None),
    account: str = Form(default=""),
    account_strategy: str = Form(default="round-robin"),
    settings: Settings = Depends(get_settings),
) -> TranscriptionResult:
    export_format = (format or settings.default_format).strip().lower()
    if export_format not in {"md", "markdown", "docx"}:
        raise HTTPException(status_code=400, detail={"message": f"Unsupported format: {export_format}", "code": "UNSUPPORTED_FORMAT"})
    normalized_format = "md" if export_format == "markdown" else export_format
    should_delete_remote = settings.delete_remote if delete_remote is None else delete_remote

    job_id = generate_job_id()
    current_job_dir = job_dir(settings.jobs_dir, job_id)
    original_name = Path(file.filename or "upload.bin").name
    input_path = current_job_dir / f"input{Path(original_name).suffix or '.bin'}"
    await persist_upload_file(file, input_path)

    payload = init_job_payload(
        job_id,
        original_filename=original_name,
        export_format=normalized_format,
        delete_remote=should_delete_remote,
        account_id=account,
        account_strategy=account_strategy,
    )
    payload["meta"]["input_file"] = str(input_path)
    payload["meta"]["job_dir"] = str(current_job_dir)
    save_job(settings.jobs_dir, job_id, payload)

    result = await run_transcription(
        settings=settings,
        job_payload=payload,
        job_dir=current_job_dir,
        input_path=input_path,
        export_format=normalized_format,
        delete_remote=should_delete_remote,
        account_id=account,
        account_strategy=account_strategy,
    )
    if result["status"] == "failed":
        raise HTTPException(status_code=500, detail=result["error"])
    return TranscriptionResult(**result)


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
    return FileResponse(path=path, filename=path.name)
