from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import os
import time
import uuid
from urllib import error as urllib_error
from urllib import request as urllib_request

from .http import download_file
from .minimal_auth import is_minimal_auth_format, is_legacy_storage_format
from .oss_upload import upload_file_to_oss
from .runtime import ExportConfig, ensure_dir, guess_mime_type, now_stamp


QUOTA_URL = "https://www.qianwen.com/zhiwen/api/equity/get_quota?c=tongyi-web"
OSS_TOKEN_URL = "https://api.qianwen.com/assistant/api/record/oss/token/get?c=tongyi-web"
UPLOAD_HEARTBEAT_URL = "https://api.qianwen.com/assistant/api/record/upload_heartbeat?c=tongyi-web"
START_RECORD_URL = "https://api.qianwen.com/assistant/api/record/start?c=tongyi-web"
POLL_URL = "https://api.qianwen.com/assistant/api/record/list/poll?c=tongyi-web"
READ_URL = "https://api.qianwen.com/assistant/api/record/read?c=tongyi-web"
DELETE_URL = "https://api.qianwen.com/assistant/api/record/task/delete?c=tongyi-web"
EXPORT_URL = "https://audio-api.qianwen.com/api/export/request?c=tongyi-web"


@dataclass(frozen=True, slots=True)
class FlowResult:
    record_id: str
    gen_record_id: str
    export_path: Path
    remote_deleted: bool


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    raw: Any
    used_upload: int
    total_upload: int
    remaining_upload: int
    gratis_upload: bool
    free: bool


@dataclass(frozen=True, slots=True)
class CookieItem:
    name: str
    value: str
    domain: str
    path: str


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


def transcript_headers(gen_record_id: str, cookie_header: str) -> dict[str, str]:
    return {
        "cookie": cookie_header,
        "referer": f"https://www.qianwen.com/efficiency/doc/transcripts/{gen_record_id}?source=2",
        "x-tw-from": "tongyi",
    }


def number_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def today_key() -> str:
    return datetime.now(UTC).date().isoformat()


def quota_state_path() -> Path:
    return Path(os.environ.get("QWEN_QUOTA_STATE_FILE", ".auth/quota-usage.json")).expanduser().resolve()


def _read_quota_state() -> tuple[Path, dict[str, Any]]:
    file_path = quota_state_path()
    try:
        parsed = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return file_path, {}
    return file_path, parsed if isinstance(parsed, dict) else {}


def _write_quota_state(records: dict[str, Any]) -> Path:
    file_path = quota_state_path()
    ensure_dir(file_path.parent)
    file_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return file_path


def _account_key(account_id: str) -> str:
    return account_id or "__default__"


def _build_daily_record(record: Any) -> dict[str, Any]:
    source = record if isinstance(record, dict) else {}
    return {
        "consumedMinutes": number_value(source.get("consumedMinutes")),
        "lastBeforeRemaining": source.get("lastBeforeRemaining"),
        "lastAfterRemaining": source.get("lastAfterRemaining"),
        "updatedAt": str(source.get("updatedAt", "")),
    }


def record_quota_consumption(
    *,
    account_id: str,
    consumed_minutes: int,
    before_snapshot: QuotaSnapshot,
    after_snapshot: QuotaSnapshot,
) -> None:
    minutes = max(0, number_value(consumed_minutes))
    _, records = _read_quota_state()
    key = _account_key(account_id)
    day = today_key()
    account_record = records.get(key, {})
    current_day = _build_daily_record(account_record.get(day))
    account_record[day] = {
        **current_day,
        "consumedMinutes": number_value(current_day.get("consumedMinutes")) + minutes,
        "lastBeforeRemaining": before_snapshot.remaining_upload,
        "lastAfterRemaining": after_snapshot.remaining_upload,
        "updatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    records[key] = account_record
    _write_quota_state(records)


def _load_auth_payload(auth_state_path: str | Path) -> dict[str, Any]:
    path = Path(auth_state_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Auth file not found: {path}")
    
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in auth file {path}: {error}") from error
    
    if not isinstance(payload, dict):
        raise ValueError("Auth file must contain a JSON object")
    
    # Validate format and provide clear error messages
    if not is_minimal_auth_format(payload) and not is_legacy_storage_format(payload):
        raise ValueError(
            f"Unsupported auth file format: {path}\n"
            f"Expected either:\n"
            f"  1. Minimal format: {{\"tongyi_sso_ticket\": \"<value>\"}}\n"
            f"  2. Legacy Playwright storage state with \"cookies\" array"
        )
    
    return payload


def _select_cookies(payload: dict[str, Any], mode: str) -> list[CookieItem]:
    if "cookies" in payload:
        items = [
            CookieItem(
                name=str(cookie.get("name", "")),
                value=str(cookie.get("value", "")),
                domain=str(cookie.get("domain", "")),
                path=str(cookie.get("path", "/")),
            )
            for cookie in payload.get("cookies", [])
            if str(cookie.get("name", "")).strip()
        ]
    elif payload.get("tongyi_sso_ticket"):
        items = [
            CookieItem(
                name="tongyi_sso_ticket",
                value=str(payload["tongyi_sso_ticket"]),
                domain=".qianwen.com",
                path="/",
            )
        ]
        if payload.get("tongyi_sso_ticket_hash"):
            items.append(
                CookieItem(
                    name="tongyi_sso_ticket_hash",
                    value=str(payload["tongyi_sso_ticket_hash"]),
                    domain=".qianwen.com",
                    path="/",
                )
            )
    else:
        items = []

    if mode == "full":
        return items
    if mode == "qianwen-only":
        return [item for item in items if "qianwen.com" in item.domain]
    if mode == "ticket-only":
        return [item for item in items if item.name == "tongyi_sso_ticket"]
    if mode == "ticket-plus-xsrf":
        keep = {"tongyi_sso_ticket", "XSRF-TOKEN"}
        return [item for item in items if item.name in keep]
    raise ValueError(f"Unsupported HTTP cookie mode: {mode}")


def _cookie_mode() -> str:
    return os.environ.get("QWEN_HTTP_COOKIE_MODE", "ticket-only").strip().lower() or "ticket-only"


def _build_cookie_header(auth_state_path: str | Path) -> str:
    payload = _load_auth_payload(auth_state_path)
    items = _select_cookies(payload, _cookie_mode())
    if not items:
        raise RuntimeError(f"No cookies selected from auth state: {auth_state_path}")
    return "; ".join(f"{item.name}={item.value}" for item in items)


def _request_cookie_headers(cookie_header: str) -> dict[str, str]:
    return {
        "cookie": cookie_header,
        "referer": "https://www.qianwen.com/discover/audioread",
    }


def _post_json_sync(url: str, payload: Any, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib_request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=60) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib_error.HTTPError as error:
        text = error.read().decode("utf-8", errors="replace")
    try:
        payload_json = json.loads(text)
    except Exception:
        payload_json = {"_raw": text[:500]}
    return payload_json


async def _post_json(url: str, payload: Any, headers: dict[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(_post_json_sync, url, payload, headers)


def _normalize_code(value: Any) -> Any:
    try:
        if value is not None and str(value).strip() != "":
            return int(str(value))
    except (TypeError, ValueError):
        return value
    return value


def _require_success(
    label: str,
    payload: dict[str, Any],
    *,
    data_required: bool = True,
    success_codes: set[int] | None = None,
) -> None:
    allowed_codes = success_codes or {0, 200}
    normalized_code = _normalize_code(payload.get("code"))
    if normalized_code not in allowed_codes:
        raise RuntimeError(f"{label} failed: code={payload.get('code')} message={payload.get('message')!r}")
    if data_required and not isinstance(payload.get("data"), dict):
        raise RuntimeError(f"{label} missing data payload: {payload}")


async def get_quota_snapshot(
    *,
    auth_state_path: str | Path,
    referer: str = "https://www.qianwen.com/discover/audioread",
) -> QuotaSnapshot:
    cookie_header = _build_cookie_header(auth_state_path)
    quota_json = await _post_json(
        QUOTA_URL,
        None,
        {
            "cookie": cookie_header,
            "referer": referer,
            "platform": "QIANWEN",
            "request-id": str(uuid.uuid4()),
            "bx-v": "2.5.36",
        },
    )
    _require_success("quota", quota_json)
    data = quota_json.get("data", {}) if isinstance(quota_json, dict) else {}
    used_upload = number_value(data.get("usedQuota", {}).get("upload"))
    total_upload = number_value(data.get("totalQuota", {}).get("upload"))
    remaining_upload = max(0, total_upload - used_upload)
    gratis_upload = str(data.get("gratisQuota", {}).get("upload", "")).lower() == "true"
    return QuotaSnapshot(
        raw=quota_json,
        used_upload=used_upload,
        total_upload=total_upload,
        remaining_upload=remaining_upload,
        gratis_upload=gratis_upload,
        free=bool(data.get("free")),
    )


async def poll_until_done(cookie_header: str, gen_record_id: str) -> dict[str, Any]:
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
    deadline = time.monotonic() + (15 * 60)
    while time.monotonic() < deadline:
        response = await _post_json(POLL_URL, payload, _request_cookie_headers(cookie_header))
        _require_success("record poll", response)
        for batch in (response.get("data") or {}).get("batchRecord", []):
            for record in batch.get("recordList", []):
                if record.get("genRecordId") != gen_record_id:
                    continue
                status = number_value(record.get("recordStatus"))
                if status == 30:
                    return record
                if status in {40, 41}:
                    detail = next(
                        (
                            str(record.get(key)).strip()
                            for key in ("failReason", "errorMessage", "errorMsg", "message", "recordStatusDesc")
                            if str(record.get(key) or "").strip()
                        ),
                        "remote task entered a terminal failure state",
                    )
                    raise RuntimeError(
                        f"Transcription failed for genRecordId={gen_record_id}: status={status} detail={detail}"
                    )
        await asyncio.sleep(5)
    raise RuntimeError(f"Polling timed out for genRecordId={gen_record_id}")


async def mark_read(cookie_header: str, record_id: str) -> None:
    payload = await _post_json(READ_URL, {"recordIds": [record_id]}, _request_cookie_headers(cookie_header))
    _require_success("record read", payload, data_required=False)


async def delete_record(cookie_header: str, record_ids: list[str]) -> bool:
    if not record_ids:
        return False
    payload = await _post_json(DELETE_URL, {"recordIds": record_ids}, _request_cookie_headers(cookie_header))
    _require_success("record delete", payload, data_required=False)
    return payload.get("data") is True


async def export_file(cookie_header: str, gen_record_id: str, export_config: ExportConfig) -> str:
    headers = transcript_headers(gen_record_id, cookie_header)
    max_attempts = max(1, int(os.environ.get("QWEN_EXPORT_MAX_RETRIES", "6")))
    initial_backoff = float(os.environ.get("QWEN_EXPORT_INITIAL_BACKOFF_SECONDS", "2"))
    export_task_id = ""
    export_start_json: Any = {}
    for attempt in range(max_attempts):
        export_start_json = await _post_json(
            EXPORT_URL,
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
        _require_success("export start", export_start_json)
        export_task_id = str((export_start_json.get("data") or {}).get("exportTaskId", "")).strip()
        if export_task_id:
            break
        message = str(export_start_json.get("message", "")).lower()
        request_too_fast = "request too fast" in message
        if not request_too_fast or attempt == max_attempts - 1:
            raise RuntimeError(f"Export start response missing exportTaskId: {export_start_json}")
        await asyncio.sleep(initial_backoff * (2**attempt))

    for _ in range(60):
        export_poll_json = await _post_json(
            EXPORT_URL,
            {
                "action": "getExportStatus",
                "exportTaskId": export_task_id,
            },
            headers,
        )
        _require_success("export poll", export_poll_json)
        data = export_poll_json.get("data", {})
        if data.get("exportStatus") == 1:
            export_urls = data.get("exportUrls", [])
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
    quota_before = await get_quota_snapshot(auth_state_path=auth_state_path)
    cookie_header = _build_cookie_header(auth_state_path)

    token_json = await _post_json(
        OSS_TOKEN_URL,
        {
            "taskType": "local",
            "useSts": 1,
            "fileSize": stats.st_size,
            "dirIdStr": "",
            "fileContentType": mime_type,
            "bizTerminal": "web",
            "tag": build_upload_tag(input_path, mime_type),
        },
        _request_cookie_headers(cookie_header),
    )
    _require_success("oss token", token_json)
    token = token_json["data"]

    await upload_file_to_oss(
        token=token,
        file_buffer=input_path,
        mime_type=mime_type,
    )

    heartbeat_json = await _post_json(
        UPLOAD_HEARTBEAT_URL,
        {"genRecordId": token["genRecordId"]},
        _request_cookie_headers(cookie_header),
    )
    _require_success("upload heartbeat", heartbeat_json, data_required=False)

    start_json = await _post_json(
        START_RECORD_URL,
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
        _request_cookie_headers(cookie_header),
    )
    _require_success("record start", start_json)

    await poll_until_done(cookie_header, token["genRecordId"])
    await mark_read(cookie_header, token["recordId"])

    if export_gate is None:
        export_url = await export_file(cookie_header, token["genRecordId"], export_config)
    else:
        async with export_gate:
            export_url = await export_file(cookie_header, token["genRecordId"], export_config)

    output_base = input_path.stem
    run_stamp = now_stamp()
    export_out = output_dir / f"{output_base}-{run_stamp}{export_config.extension}"
    ensure_dir(output_dir)
    await download_file(export_url, export_out)

    deleted = False
    if should_delete:
        deleted = await delete_record(cookie_header, [token["recordId"]])

    quota_after = await get_quota_snapshot(auth_state_path=auth_state_path)
    consumed_minutes = max(0, quota_before.remaining_upload - quota_after.remaining_upload)
    record_quota_consumption(
        account_id=account_id,
        consumed_minutes=consumed_minutes,
        before_snapshot=quota_before,
        after_snapshot=quota_after,
    )
    return FlowResult(
        record_id=token["recordId"],
        gen_record_id=token["genRecordId"],
        export_path=export_out,
        remote_deleted=deleted,
    )
