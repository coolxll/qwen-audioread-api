from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ErrorDetail(BaseModel):
    message: str
    code: str = "INTERNAL_ERROR"


class TranscriptionResult(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    format: Literal["md"]
    markdown_filename: str | None = None
    suggested_poll_after_seconds: int | None = None
    content_type: str | None = None
    text: str | None = None
    output_file: str | None = None
    download_url: str | None = None
    record_id: str | None = None
    gen_record_id: str | None = None
    remote_deleted: bool | None = None
    account_id: str | None = None
    account_label: str | None = None
    original_filename: str
    created_at: str
    updated_at: str
    completed_at: str | None = None
    error: ErrorDetail | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class TranscriptionListResponse(BaseModel):
    items: list[TranscriptionResult]
    total: int


class BatchJobItem(BaseModel):
    job_id: str
    original_filename: str
    markdown_filename: str
    status: Literal["queued", "running", "succeeded", "failed"]
    job_url: str
    download_url: str
    suggested_poll_after_seconds: int


class BatchTranscriptionResponse(BaseModel):
    batch_id: str
    total: int
    accepted: int
    format: Literal["md"]
    output_dir: str
    items: list[BatchJobItem]
