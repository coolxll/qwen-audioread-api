from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os

from playwright.async_api import async_playwright

from .http import api_json, download_file
from .oss_upload import upload_file_to_oss
from .quota import get_quota_snapshot, record_quota_consumption
from .runtime import ExportConfig, ensure_dir, env_flag, guess_mime_type, now_stamp


@dataclass(frozen=True, slots=True)
class FlowResult:
    record_id: str
    gen_record_id: str
    export_path: Path
    remote_deleted: bool


def build_upload_tag(file_path: str | Path, mime_type: str) -> dict[str, Any]:
    parsed = Path(file_path)
    is_video = 1 if mime_type.startswith("video/") else 0
    return {
        "showName": parsed.stem,
        "fileFormat": parsed.suffix.removeprefix("."),
        "fileType": "local",
        "lang": "cn",
        "roleSplitNum": -1,
        "translateSwitch": 0,
        "transTargetValue": 0,
        "originalTag": json.dumps({"isVideo": is_video}),
        "client": "web",
    }


def transcript_headers(gen_record_id: str) -> dict[str, str]:
    return {
        "referer": f"https://www.qianwen.com/efficiency/doc/transcripts/{gen_record_id}?source=2",
        "x-tw-from": "tongyi",
    }


async def poll_until_done(context: Any, gen_record_id: str) -> dict[str, Any]:
    url = "https://api.qianwen.com/assistant/api/record/list/poll?c=tongyi-web"
    payload = {
        "status": [10, 20, 30, 40, 41],
        "fileTypes": [],
        "beginTime": "",
        "mediaType": "",
        "endTime": "",
        "showName": "",
        "read": "",
        "lang": "",
        "shareUserId": "",
        "pageNo": 1,
        "pageSize": 1000,
        "recordSources": ["chat", "zhiwen", "tingwu"],
        "taskTypes": ["local", "net_source", "doc_read", "url_read", "paper_read", "book_read", "doc_convert"],
        "terminal": "web",
        "module": "uploadhistory",
    }
    deadline = asyncio.get_running_loop().time() + (15 * 60)
    while asyncio.get_running_loop().time() < deadline:
        response = await api_json(context, url, payload)
        for batch in response.get("data", {}).get("batchRecord", []):
            for record in batch.get("recordList", []):
                if record.get("genRecordId") == gen_record_id and record.get("recordStatus") == 30:
                    return record
        await asyncio.sleep(5)
    raise RuntimeError(f"Polling timed out for genRecordId={gen_record_id}")


async def delete_record(context: Any, record_ids: list[str]) -> bool:
    if not record_ids:
        return False
    response = await api_json(
        context,
        "https://api.qianwen.com/assistant/api/record/task/delete?c=tongyi-web",
        {"recordIds": record_ids},
    )
    return response.get("data") is True


async def export_file(context: Any, gen_record_id: str, export_config: ExportConfig) -> str:
    headers = transcript_headers(gen_record_id)
    max_attempts = max(1, int(os.environ.get("QWEN_EXPORT_MAX_RETRIES", "6")))
    initial_backoff = float(os.environ.get("QWEN_EXPORT_INITIAL_BACKOFF_SECONDS", "2"))
    export_task_id = ""
    export_start_json: Any = {}
    for attempt in range(max_attempts):
        export_start_json = await api_json(
            context,
            "https://audio-api.qianwen.com/api/export/request?c=tongyi-web",
            {
                "action": "exportTrans",
                "transIds": [gen_record_id],
                "exportDetails": [
                    {
                        "docType": 1,
                        "fileType": export_config.file_type,
                        "withSpeaker": True,
                        "withTimeStamp": True,
                    }
                ],
            },
            headers,
        )
        export_task_id = str(export_start_json.get("data", {}).get("exportTaskId", "")).strip()
        if export_task_id:
            break
        code = str(export_start_json.get("code", ""))
        message = str(export_start_json.get("message", "")).lower()
        request_too_fast = code == "EPO.RequestTooFast" or "request too fast" in message
        if not request_too_fast or attempt == max_attempts - 1:
            raise RuntimeError(f"Export start response missing exportTaskId: {export_start_json}")
        await asyncio.sleep(initial_backoff * (2**attempt))

    if not export_task_id:
        raise RuntimeError(f"Export start response missing exportTaskId: {export_start_json}")

    for _ in range(60):
        export_poll_json = await api_json(
            context,
            "https://audio-api.qianwen.com/api/export/request?c=tongyi-web",
            {
                "action": "getExportStatus",
                "exportTaskId": export_task_id,
            },
            headers,
        )
        if export_poll_json.get("data", {}).get("exportStatus") == 1:
            export_urls = export_poll_json.get("data", {}).get("exportUrls", [])
            export_url = export_urls[0].get("url", "") if export_urls else ""
            if export_url:
                return export_url
        await asyncio.sleep(5)

    raise RuntimeError(f"Export did not produce a downloadable URL for exportTaskId={export_task_id}")


async def run_real_flow(
    *,
    file_path: str | Path,
    auth_state_path: str | Path,
    download_dir: str | Path,
    export_config: ExportConfig,
    should_delete: bool = False,
    account_id: str = "",
    export_gate: asyncio.Semaphore | None = None,
) -> FlowResult:
    input_path = Path(file_path).resolve()
    output_dir = Path(download_dir).resolve()
    mime_type = guess_mime_type(input_path)
    stats = input_path.stat()
    file_buffer = input_path.read_bytes()
    quota_before = await get_quota_snapshot(auth_state_path=auth_state_path)
    log = _make_flow_logger(input_path.name)

    async with async_playwright() as playwright:
        api = await playwright.request.new_context(storage_state=str(Path(auth_state_path).resolve()))
        try:
            log(f"Using file: {input_path}")
            log(f"File size: {stats.st_size}")
            log(f"quota upload remaining: {quota_before.remaining_upload}/{quota_before.total_upload}")

            token_json = await api_json(
                api,
                "https://api.qianwen.com/assistant/api/record/oss/token/get?c=tongyi-web",
                {
                    "taskType": "local",
                    "useSts": 1,
                    "fileSize": stats.st_size,
                    "dirIdStr": "",
                    "fileContentType": mime_type,
                    "bizTerminal": "web",
                    "tag": build_upload_tag(input_path, mime_type),
                },
            )
            token = token_json["data"]
            log(f"genRecordId: {token['genRecordId']}")
            log(f"recordId: {token['recordId']}")

            await upload_file_to_oss(
                token=token,
                file_buffer=file_buffer,
                mime_type=mime_type,
                on_progress=_make_upload_progress_logger(log),
            )

            await api_json(
                api,
                "https://api.qianwen.com/assistant/api/record/upload_heartbeat?c=tongyi-web",
                {"genRecordId": token["genRecordId"]},
            )
            log("upload heartbeat sent")

            start_json = await api_json(
                api,
                "https://api.qianwen.com/assistant/api/record/start?c=tongyi-web",
                {
                    "taskType": "local",
                    "tingwuRequest": {
                        "fileLink": token["getLink"],
                        "transId": token["genRecordId"],
                        "fileSize": stats.st_size,
                    },
                    "bizTerminal": "web",
                    "dirIdStr": "",
                },
            )
            log(f"started batchId={start_json['data']['batchId']}")

            completed_record = await poll_until_done(api, token["genRecordId"])
            log(f"record completed with status={completed_record['recordStatus']}")

            await api_json(
                api,
                "https://api.qianwen.com/assistant/api/record/read?c=tongyi-web",
                {"recordIds": [token["recordId"]]},
            )

            if export_gate is None:
                export_url = await export_file(api, token["genRecordId"], export_config)
            else:
                async with export_gate:
                    export_url = await export_file(api, token["genRecordId"], export_config)
            output_base = input_path.stem
            run_stamp = now_stamp()
            export_out = output_dir / f"{output_base}-{run_stamp}{export_config.extension}"

            ensure_dir(output_dir)
            await download_file(export_url, export_out)

            log(f"{export_config.label} saved: {export_out}")

            if env_flag("QWEN_SAVE_DEBUG_JSON", default=False):
                headers = transcript_headers(token["genRecordId"])
                transcript_json = await api_json(
                    api,
                    "https://audio-api.qianwen.com/api/trans/getTransResult?c=tongyi-web",
                    {
                        "action": "getTransResult",
                        "version": "1.0",
                        "transId": token["genRecordId"],
                    },
                    headers,
                )
                doc_edit_json = await api_json(
                    api,
                    "https://audio-api.qianwen.com/api/doc/getTransDocEdit?c=tongyi-web",
                    {
                        "action": "getTransDocEdit",
                        "version": "1.0",
                        "transId": token["genRecordId"],
                    },
                    headers,
                )
                transcript_out = output_dir / f"{output_base}-{run_stamp}-transcript.json"
                doc_edit_out = output_dir / f"{output_base}-{run_stamp}-doc-edit.json"
                transcript_out.write_text(json.dumps(transcript_json, indent=2), encoding="utf-8")
                doc_edit_out.write_text(json.dumps(doc_edit_json, indent=2), encoding="utf-8")
                log(f"transcript saved: {transcript_out}")
                log(f"doc edit saved: {doc_edit_out}")

            deleted = False
            if should_delete:
                deleted = await delete_record(api, [token["recordId"]])
                log(f"delete status: {'success' if deleted else 'failed'}")

            quota_after = await get_quota_snapshot(auth_state_path=auth_state_path)
            consumed_minutes = max(0, quota_before.remaining_upload - quota_after.remaining_upload)
            record_quota_consumption(
                account_id=account_id,
                consumed_minutes=consumed_minutes,
                before_snapshot=quota_before,
                after_snapshot=quota_after,
            )
            log(f"quota upload remaining after run: {quota_after.remaining_upload}/{quota_after.total_upload}")
            log(f"tracked quota consumption for this run: {consumed_minutes} minutes")
            return FlowResult(
                record_id=token["recordId"],
                gen_record_id=token["genRecordId"],
                export_path=export_out,
                remote_deleted=deleted,
            )
        finally:
            await api.dispose()


def _make_flow_logger(file_name: str):
    def log(message: str) -> None:
        print(f"[{file_name}] {message}")

    return log


def _make_upload_progress_logger(log):
    last_bucket = -1

    def handle_event(event: dict[str, Any]) -> None:
        nonlocal last_bucket
        event_type = event.get("type")
        if event_type == "direct-upload-complete":
            log("direct presigned upload completed")
        elif event_type == "direct-upload-failed":
            error = event.get("error")
            if event.get("mode") == "auto":
                log(f"direct upload failed, falling back to multipart: {error}")
            else:
                log(f"direct upload failed: {error}")
        elif event_type == "multipart-started":
            log(f"uploadId: {event.get('uploadId')}")
        elif event_type == "part-uploaded":
            part_number = int(event.get("partNumber") or 0)
            total_parts = int(event.get("totalParts") or 0)
            if total_parts <= 0:
                return
            percent = max(1, round(part_number * 100 / total_parts))
            bucket = min(10, percent // 10)
            should_log = part_number == 1 or part_number == total_parts or bucket > last_bucket
            if should_log:
                last_bucket = bucket
                log(f"upload progress: {part_number}/{total_parts} ({percent}%)")
        elif event_type == "multipart-complete":
            log("multipart upload completed")

    return handle_event
