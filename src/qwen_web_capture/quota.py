from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import json
import os
import uuid

from playwright.async_api import async_playwright

from .http import api_json
from .runtime import as_absolute, ensure_dir


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    raw: Any
    used_upload: int
    total_upload: int
    remaining_upload: int
    gratis_upload: bool
    free: bool


@dataclass(frozen=True, slots=True)
class ClaimEquityResult:
    claimed: bool
    skipped: bool
    reason: str
    before_snapshot: QuotaSnapshot | None
    after_snapshot: QuotaSnapshot | None


def number_value(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed


def today_key() -> str:
    return datetime.now(UTC).date().isoformat()


def quota_state_path() -> Path:
    return as_absolute(os.environ.get("QWEN_QUOTA_STATE_FILE", ".auth/quota-usage.json"))


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


def account_key(account_id: str) -> str:
    return account_id or "__default__"


def build_daily_record(record: Any) -> dict[str, Any]:
    source = record if isinstance(record, dict) else {}
    return {
        "consumedMinutes": number_value(source.get("consumedMinutes")),
        "lastBeforeRemaining": source.get("lastBeforeRemaining"),
        "lastAfterRemaining": source.get("lastAfterRemaining"),
        "lastEquityClaimAt": str(source.get("lastEquityClaimAt", "")),
        "lastEquityBeforeRemaining": source.get("lastEquityBeforeRemaining"),
        "lastEquityAfterRemaining": source.get("lastEquityAfterRemaining"),
        "updatedAt": str(source.get("updatedAt", "")),
    }


async def get_quota_snapshot(
    *,
    auth_state_path: str | Path,
    referer: str = "https://www.qianwen.com/discover/audioread",
) -> QuotaSnapshot:
    async with async_playwright() as playwright:
        api = await playwright.request.new_context(storage_state=str(as_absolute(auth_state_path)))
        try:
            quota_json = await api_json(
                api,
                "https://www.qianwen.com/zhiwen/api/equity/get_quota?c=tongyi-web",
                None,
                {
                    "referer": referer,
                    "platform": "QIANWEN",
                    "request-id": str(uuid.uuid4()),
                    "bx-v": "2.5.36",
                },
            )
        finally:
            await api.dispose()

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


def record_quota_consumption(
    *,
    account_id: str,
    consumed_minutes: int,
    before_snapshot: QuotaSnapshot,
    after_snapshot: QuotaSnapshot,
) -> None:
    minutes = max(0, number_value(consumed_minutes))
    _, records = _read_quota_state()
    key = account_key(account_id)
    day = today_key()
    account_record = records.get(key, {})
    current_day = build_daily_record(account_record.get(day))
    account_record[day] = {
        **current_day,
        "consumedMinutes": number_value(current_day.get("consumedMinutes")) + minutes,
        "lastBeforeRemaining": before_snapshot.remaining_upload,
        "lastAfterRemaining": after_snapshot.remaining_upload,
        "updatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    records[key] = account_record
    _write_quota_state(records)


def get_daily_quota_record(account_id: str) -> dict[str, Any]:
    _, records = _read_quota_state()
    return build_daily_record(records.get(account_key(account_id), {}).get(today_key()))


async def visit_equity_page(auth_state_path: str | Path) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = await browser.new_context(storage_state=str(as_absolute(auth_state_path)))
            page = await context.new_page()
            await page.goto("https://www.qianwen.com/equity", wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(3000)
            await context.close()
        finally:
            await browser.close()


def has_claimed_equity_today(account_id: str) -> bool:
    daily = get_daily_quota_record(account_id)
    return bool(daily.get("lastEquityClaimAt"))


def _write_equity_claim_record(
    *,
    account_id: str,
    before_snapshot: QuotaSnapshot,
    after_snapshot: QuotaSnapshot,
) -> None:
    _, records = _read_quota_state()
    key = account_key(account_id)
    day = today_key()
    account_record = records.get(key, {})
    current_day = build_daily_record(account_record.get(day))
    account_record[day] = {
        **current_day,
        "lastEquityClaimAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "lastEquityBeforeRemaining": before_snapshot.remaining_upload,
        "lastEquityAfterRemaining": after_snapshot.remaining_upload,
        "updatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    records[key] = account_record
    _write_quota_state(records)


async def claim_equity_quota(
    *,
    account_id: str,
    auth_state_path: str | Path,
    force: bool = False,
) -> ClaimEquityResult:
    if not force and has_claimed_equity_today(account_id):
        return ClaimEquityResult(
            claimed=False,
            skipped=True,
            reason="already-claimed-today",
            before_snapshot=None,
            after_snapshot=None,
        )

    before_snapshot = await get_quota_snapshot(
        auth_state_path=auth_state_path,
        referer="https://www.qianwen.com/equity",
    )
    await visit_equity_page(auth_state_path)
    after_snapshot = await get_quota_snapshot(
        auth_state_path=auth_state_path,
        referer="https://www.qianwen.com/equity",
    )
    _write_equity_claim_record(
        account_id=account_id,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
    )
    return ClaimEquityResult(
        claimed=True,
        skipped=False,
        reason="",
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
    )
