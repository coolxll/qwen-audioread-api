from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from qwen_web_capture.oss_upload import upload_file_to_oss


QUOTA_URL = "https://www.qianwen.com/zhiwen/api/equity/get_quota?c=tongyi-web"
OSS_TOKEN_URL = "https://api.qianwen.com/assistant/api/record/oss/token/get?c=tongyi-web"
UPLOAD_HEARTBEAT_URL = "https://api.qianwen.com/assistant/api/record/upload_heartbeat?c=tongyi-web"
START_RECORD_URL = "https://api.qianwen.com/assistant/api/record/start?c=tongyi-web"
POLL_URL = "https://api.qianwen.com/assistant/api/record/list/poll?c=tongyi-web"
READ_URL = "https://api.qianwen.com/assistant/api/record/read?c=tongyi-web"
DELETE_URL = "https://api.qianwen.com/assistant/api/record/task/delete?c=tongyi-web"
EXPORT_URL = "https://audio-api.qianwen.com/api/export/request?c=tongyi-web"


@dataclass(frozen=True, slots=True)
class CookieItem:
    name: str
    value: str
    domain: str
    path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe Qianwen HTTP auth viability without Playwright by reusing cookies from a storage-state file."
    )
    parser.add_argument(
        "--action",
        choices=["auth", "start", "full", "resume"],
        default="auth",
        help="auth: probe quota/token; start: upload file then heartbeat/start; full: full flow; resume: continue from existing record ids.",
    )
    parser.add_argument(
        "--auth-state",
        default=".auth/qwen-storage-state.json",
        help="Playwright storage-state JSON path.",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "qianwen-only", "ticket-only", "ticket-plus-xsrf"],
        default="ticket-only",
        help="Which subset of cookies to send.",
    )
    parser.add_argument(
        "--file-size",
        type=int,
        default=1024,
        help="Synthetic file size for OSS token probe.",
    )
    parser.add_argument(
        "--file",
        help="Local media file path for --action start.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print safe cookie summary before probing.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Polling interval seconds for --action=full.",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=900.0,
        help="Polling timeout seconds for --action=full.",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/qwen2api-http-runtime-output",
        help="Output directory for downloaded markdown when --action=full.",
    )
    parser.add_argument(
        "--record-id",
        help="Existing recordId for --action=resume.",
    )
    parser.add_argument(
        "--gen-record-id",
        help="Existing genRecordId for --action=resume.",
    )
    return parser.parse_args()


def load_storage_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_cookies(cookies: list[dict[str, Any]], mode: str) -> list[CookieItem]:
    items = [
        CookieItem(
            name=str(cookie.get("name", "")),
            value=str(cookie.get("value", "")),
            domain=str(cookie.get("domain", "")),
            path=str(cookie.get("path", "/")),
        )
        for cookie in cookies
        if str(cookie.get("name", "")).strip()
    ]
    if mode == "full":
        return items
    if mode == "qianwen-only":
        return [item for item in items if "qianwen.com" in item.domain]
    if mode == "ticket-only":
        keep = {"tongyi_sso_ticket"}
        return [item for item in items if item.name in keep]
    if mode == "ticket-plus-xsrf":
        keep = {"tongyi_sso_ticket", "XSRF-TOKEN"}
        return [item for item in items if item.name in keep]
    raise ValueError(f"Unsupported mode: {mode}")


def build_cookie_header(items: list[CookieItem]) -> str:
    return "; ".join(f"{item.name}={item.value}" for item in items)


def post_json(url: str, payload: Any, headers: dict[str, str]) -> tuple[int, dict[str, Any], str]:
    request = urllib_request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(text), text
    except urllib_error.HTTPError as error:
        text = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except Exception:
            payload = {"_raw": text[:500]}
        return error.code, payload, text


def require_success(
    label: str,
    payload: dict[str, Any],
    *,
    data_required: bool = True,
    success_codes: set[int] | None = None,
) -> None:
    allowed_codes = success_codes or {0, 200}
    code = payload.get("code")
    normalized_code = code
    try:
        if code is not None and str(code).strip() != "":
            normalized_code = int(str(code))
    except (TypeError, ValueError):
        normalized_code = code
    if normalized_code not in allowed_codes:
        raise RuntimeError(f"{label} failed: code={code} message={payload.get('message')!r}")
    if data_required and not isinstance(payload.get("data"), dict):
        raise RuntimeError(f"{label} missing data payload: {payload}")


def summarize_cookie_items(items: list[CookieItem]) -> dict[str, Any]:
    return {
        "count": len(items),
        "names": sorted({item.name for item in items}),
        "domains": sorted({item.domain for item in items}),
    }


def quota_headers(cookie_header: str) -> dict[str, str]:
    return {
        "cookie": cookie_header,
        "referer": "https://www.qianwen.com/discover/audioread",
        "platform": "QIANWEN",
        "request-id": str(uuid.uuid4()),
        "bx-v": "2.5.36",
    }


def probe_quota(cookie_header: str) -> dict[str, Any]:
    status, payload, _raw = post_json(QUOTA_URL, None, quota_headers(cookie_header))
    return {
        "http_status": status,
        "code": payload.get("code"),
        "message": payload.get("message"),
        "has_data": isinstance(payload.get("data"), dict),
        "remaining_upload": ((payload.get("data") or {}).get("totalQuota") or {}).get("upload"),
    }


def probe_oss_token(cookie_header: str, file_size: int) -> dict[str, Any]:
    status, payload, _raw = post_json(
        OSS_TOKEN_URL,
        {
            "taskType": "local",
            "useSts": 1,
            "fileSize": file_size,
            "dirIdStr": "",
            "fileContentType": "video/mp4",
            "bizTerminal": "web",
            "tag": {
                "showName": "probe",
                "fileFormat": "mp4",
                "fileType": "local",
                "lang": "cn",
                "roleSplitNum": -1,
                "translateSwitch": 0,
                "transTargetValue": 0,
                "originalTag": "{\"isVideo\": 1}",
                "client": "web",
            },
        },
        {"cookie": cookie_header},
    )
    data = payload.get("data") or {}
    return {
        "http_status": status,
        "code": payload.get("code"),
        "message": payload.get("message"),
        "has_data": isinstance(data, dict),
        "has_genRecordId": bool(data.get("genRecordId")),
        "has_recordId": bool(data.get("recordId")),
    }


def guess_mime_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".m4a":
        return "audio/mp4"
    if suffix == ".mov":
        return "video/quicktime"
    return "application/octet-stream"


def build_upload_tag(file_path: Path, mime_type: str) -> dict[str, Any]:
    is_video = 1 if mime_type.startswith("video/") else 0
    return {
        "showName": file_path.stem,
        "fileFormat": file_path.suffix.removeprefix("."),
        "fileType": "local",
        "lang": "cn",
        "roleSplitNum": -1,
        "translateSwitch": 0,
        "transTargetValue": 0,
        "originalTag": json.dumps({"isVideo": is_video}),
        "client": "web",
    }


def request_cookie_headers(cookie_header: str) -> dict[str, str]:
    return {
        "cookie": cookie_header,
        "referer": "https://www.qianwen.com/discover/audioread",
    }


def transcript_headers(gen_record_id: str, cookie_header: str) -> dict[str, str]:
    return {
        "cookie": cookie_header,
        "referer": f"https://www.qianwen.com/efficiency/doc/transcripts/{gen_record_id}?source=2",
        "x-tw-from": "tongyi",
    }


def download_file(url: str, output_path: Path) -> Path:
    req = urllib_request.Request(url, method="GET")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib_request.urlopen(req, timeout=60) as response:
        output_path.write_bytes(response.read())
    return output_path


async def run_start_probe(cookie_header: str, file_path: Path) -> dict[str, Any]:
    file_path = file_path.expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    file_buffer = file_path.read_bytes()
    mime_type = guess_mime_type(file_path)
    _, token_payload, _ = post_json(
        OSS_TOKEN_URL,
        {
            "taskType": "local",
            "useSts": 1,
            "fileSize": len(file_buffer),
            "dirIdStr": "",
            "fileContentType": mime_type,
            "bizTerminal": "web",
            "tag": build_upload_tag(file_path, mime_type),
        },
        request_cookie_headers(cookie_header),
    )
    require_success("oss token", token_payload)
    token_data = token_payload["data"]

    progress_events: list[dict[str, Any]] = []

    def on_progress(event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "part-uploaded":
            progress_events.append(
                {
                    "type": event_type,
                    "partNumber": event.get("partNumber"),
                    "totalParts": event.get("totalParts"),
                }
            )
        elif event_type in {"multipart-started", "multipart-complete", "direct-upload-complete", "direct-upload-failed"}:
            progress_events.append({"type": event_type})

    await upload_file_to_oss(
        token=token_data,
        file_buffer=file_buffer,
        mime_type=mime_type,
        on_progress=on_progress,
    )

    _, heartbeat_payload, _ = post_json(
        UPLOAD_HEARTBEAT_URL,
        {"genRecordId": token_data["genRecordId"]},
        request_cookie_headers(cookie_header),
    )
    require_success("upload heartbeat", heartbeat_payload, data_required=False)

    _, start_payload, _ = post_json(
        START_RECORD_URL,
        {
            "taskType": "local",
            "tingwuRequest": {
                "fileLink": token_data["getLink"],
                "transId": token_data["genRecordId"],
                "fileSize": len(file_buffer),
            },
            "bizTerminal": "web",
            "dirIdStr": "",
        },
        request_cookie_headers(cookie_header),
    )
    require_success("record start", start_payload)

    return {
        "file_path": str(file_path),
        "file_size": len(file_buffer),
        "mime_type": mime_type,
        "record_id": token_data.get("recordId"),
        "gen_record_id": token_data.get("genRecordId"),
        "batch_id": (start_payload.get("data") or {}).get("batchId"),
        "heartbeat_code": heartbeat_payload.get("code"),
        "start_code": start_payload.get("code"),
        "progress_summary": {
            "event_count": len(progress_events),
            "last_events": progress_events[-5:],
        },
    }


def poll_until_done(cookie_header: str, gen_record_id: str, *, timeout_seconds: float, interval_seconds: float) -> dict[str, Any]:
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
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _status, response, _raw = post_json(POLL_URL, payload, request_cookie_headers(cookie_header))
        for batch in (response.get("data") or {}).get("batchRecord", []):
            for record in batch.get("recordList", []):
                if record.get("genRecordId") == gen_record_id:
                    if record.get("recordStatus") == 30:
                        return {
                            "found": True,
                            "record_status": record.get("recordStatus"),
                            "record_id": record.get("recordId"),
                            "gen_record_id": record.get("genRecordId"),
                            "show_name": record.get("showName"),
                        }
        time.sleep(interval_seconds)
    raise TimeoutError(f"Polling timed out for genRecordId={gen_record_id}")


def export_markdown(cookie_header: str, gen_record_id: str, *, output_dir: Path) -> dict[str, Any]:
    headers = transcript_headers(gen_record_id, cookie_header)
    _status, start_payload, _raw = post_json(
        EXPORT_URL,
        {
            "action": "exportTrans",
            "transIds": [gen_record_id],
            "exportDetails": [
                {
                    "docType": 1,
                    "fileType": 3,
                    "withSpeaker": True,
                    "withTimeStamp": True,
                }
            ],
        },
        headers,
    )
    require_success("export start", start_payload)
    export_task_id = str((start_payload.get("data") or {}).get("exportTaskId", "")).strip()
    if not export_task_id:
        raise RuntimeError(f"Missing exportTaskId: {start_payload}")

    deadline = time.monotonic() + 300
    export_url = ""
    while time.monotonic() < deadline:
        _poll_status, poll_payload, _raw = post_json(
            EXPORT_URL,
            {
                "action": "getExportStatus",
                "exportTaskId": export_task_id,
            },
            headers,
        )
        data = poll_payload.get("data") or {}
        if data.get("exportStatus") == 1:
            export_urls = data.get("exportUrls") or []
            export_url = str(export_urls[0].get("url", "")).strip() if export_urls else ""
            if export_url:
                break
        time.sleep(5)
    if not export_url:
        raise TimeoutError(f"Export URL not ready for exportTaskId={export_task_id}")

    output_path = output_dir / f"{gen_record_id}.md"
    download_file(export_url, output_path)
    return {
        "export_task_id": export_task_id,
        "download_url": export_url,
        "output_path": str(output_path),
        "output_size": output_path.stat().st_size,
    }


def mark_read(cookie_header: str, record_id: str) -> dict[str, Any]:
    _status, payload, _raw = post_json(READ_URL, {"recordIds": [record_id]}, request_cookie_headers(cookie_header))
    return {
        "code": payload.get("code"),
        "message": payload.get("message"),
    }


def delete_record(cookie_header: str, record_id: str) -> dict[str, Any]:
    _status, payload, _raw = post_json(DELETE_URL, {"recordIds": [record_id]}, request_cookie_headers(cookie_header))
    return {
        "code": payload.get("code"),
        "message": payload.get("message"),
        "data": payload.get("data"),
    }


async def run_full_probe(
    cookie_header: str,
    file_path: Path,
    *,
    timeout_seconds: float,
    interval_seconds: float,
    output_dir: Path,
) -> dict[str, Any]:
    start_result = await run_start_probe(cookie_header, file_path)
    poll_result = poll_until_done(
        cookie_header,
        start_result["gen_record_id"],
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )
    read_result = mark_read(cookie_header, start_result["record_id"])
    export_result = export_markdown(cookie_header, start_result["gen_record_id"], output_dir=output_dir)
    delete_result = delete_record(cookie_header, start_result["record_id"])
    return {
        "start_probe": start_result,
        "poll_probe": poll_result,
        "read_probe": read_result,
        "export_probe": export_result,
        "delete_probe": delete_result,
    }


def run_resume_probe(cookie_header: str, *, record_id: str, gen_record_id: str, output_dir: Path) -> dict[str, Any]:
    read_result = mark_read(cookie_header, record_id)
    export_result = export_markdown(cookie_header, gen_record_id, output_dir=output_dir)
    delete_result = delete_record(cookie_header, record_id)
    return {
        "record_id": record_id,
        "gen_record_id": gen_record_id,
        "read_probe": read_result,
        "export_probe": export_result,
        "delete_probe": delete_result,
    }


def main() -> int:
    args = parse_args()
    auth_state_path = Path(args.auth_state).expanduser().resolve()
    storage_state = load_storage_state(auth_state_path)
    selected = select_cookies(storage_state.get("cookies", []), args.mode)
    if not selected:
        raise SystemExit(f"No cookies selected for mode={args.mode}")
    if args.verbose:
        print(
            json.dumps(
                {
                    "auth_state_path": str(auth_state_path),
                    "mode": args.mode,
                    "cookie_summary": summarize_cookie_items(selected),
                    "origin_count": len(storage_state.get("origins", [])),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    cookie_header = build_cookie_header(selected)
    result: dict[str, Any] = {
        "auth_state_path": str(auth_state_path),
        "mode": args.mode,
        "action": args.action,
        "cookie_summary": summarize_cookie_items(selected),
        "quota_probe": probe_quota(cookie_header),
        "oss_token_probe": probe_oss_token(cookie_header, args.file_size),
    }
    if args.action == "start":
        if not args.file:
            raise SystemExit("--file is required when --action=start")
        result["start_probe"] = asyncio.run(run_start_probe(cookie_header, Path(args.file)))
    if args.action == "full":
        if not args.file:
            raise SystemExit("--file is required when --action=full")
        result["full_probe"] = asyncio.run(
            run_full_probe(
                cookie_header,
                Path(args.file),
                timeout_seconds=args.poll_timeout,
                interval_seconds=args.poll_interval,
                output_dir=Path(args.output_dir).expanduser().resolve(),
            )
        )
    if args.action == "resume":
        if not args.record_id or not args.gen_record_id:
            raise SystemExit("--record-id and --gen-record-id are required when --action=resume")
        result["resume_probe"] = run_resume_probe(
            cookie_header,
            record_id=args.record_id,
            gen_record_id=args.gen_record_id,
            output_dir=Path(args.output_dir).expanduser().resolve(),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
