from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
import os

from .runtime import as_absolute, ensure_dir


@dataclass(frozen=True, slots=True)
class ExecutionAccount:
    account_id: str
    account_label: str
    auth_state_path: Path
    accounts_file_path: Path


@dataclass(frozen=True, slots=True)
class ExecutionAccounts:
    strategy: str
    pool_state_path: Path
    accounts: list[ExecutionAccount]


@dataclass(frozen=True, slots=True)
class ConfiguredAccount:
    id: str
    label: str
    storage_state_path: str


def normalize_account_strategy(strategy: str | None) -> str:
    normalized = str(strategy or os.environ.get("QWEN_ACCOUNT_STRATEGY", "round-robin")).strip().lower()
    if normalized not in {"round-robin", "failover", "sticky"}:
        raise ValueError(f"Unsupported account strategy: {strategy}")
    return normalized


def _accounts_config_path(config_path: str | Path | None = None) -> Path:
    raw = config_path or os.environ.get("QWEN_ACCOUNTS_FILE", "accounts.json")
    return as_absolute(raw)


def _normalize_account_entry(entry: object) -> ConfiguredAccount | None:
    if not isinstance(entry, dict):
        return None
    account_id = str(entry.get("id", "")).strip()
    storage_state_path = str(entry.get("storageStatePath", "")).strip()
    if not account_id or not storage_state_path:
        return None
    label = str(entry.get("label", "")).strip() or account_id
    return ConfiguredAccount(id=account_id, label=label, storage_state_path=storage_state_path)


def load_accounts_config(config_path: str | Path | None = None) -> tuple[Path, list[ConfiguredAccount]]:
    path = _accounts_config_path(config_path)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return path, []
    if not isinstance(parsed, list):
        raise ValueError("accounts file must be a JSON array")
    accounts = [account for item in parsed if (account := _normalize_account_entry(item))]
    return path, accounts


def resolve_auth_state_path(
    *,
    account_id: str = "",
    fallback_path: str | Path = ".auth/qwen-storage-state.json",
) -> ExecutionAccount:
    selected_account_id = str(account_id).strip()
    accounts_file_path = _accounts_config_path()
    if not selected_account_id:
        return ExecutionAccount(
            account_id="",
            account_label="",
            auth_state_path=as_absolute(os.environ.get("QWEN_AUTH_STATE_PATH", fallback_path)),
            accounts_file_path=accounts_file_path,
        )

    config_path, accounts = load_accounts_config(accounts_file_path)
    matched = next((account for account in accounts if account.id == selected_account_id), None)
    if not matched:
        known_accounts = ", ".join(account.id for account in accounts)
        if known_accounts:
            raise ValueError(f"Unknown account '{selected_account_id}'. Known accounts: {known_accounts}")
        raise ValueError(f"Unknown account '{selected_account_id}'. No accounts found in {config_path}")

    return ExecutionAccount(
        account_id=matched.id,
        account_label=matched.label,
        auth_state_path=as_absolute(matched.storage_state_path),
        accounts_file_path=config_path,
    )


def _pool_state_path() -> Path:
    return as_absolute(os.environ.get("QWEN_ACCOUNT_POOL_STATE_FILE", ".auth/account-pool-state.json"))


def _read_pool_state() -> dict[str, str]:
    state_path = _pool_state_path()
    try:
        parsed = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "statePath": str(state_path),
            "lastSuccessfulAccountId": "",
            "updatedAt": "",
        }
    return {
        "statePath": str(state_path),
        "lastSuccessfulAccountId": str(parsed.get("lastSuccessfulAccountId", "")),
        "updatedAt": str(parsed.get("updatedAt", "")),
    }


def _rotate_accounts(accounts: list[ConfiguredAccount], pivot_account_id: str) -> list[ConfiguredAccount]:
    if not pivot_account_id or len(accounts) <= 1:
        return accounts
    pivot_index = next((index for index, account in enumerate(accounts) if account.id == pivot_account_id), -1)
    if pivot_index < 0:
        return accounts
    start_index = (pivot_index + 1) % len(accounts)
    return [*accounts[start_index:], *accounts[:start_index]]


def resolve_execution_accounts(
    *,
    account_id: str = "",
    fallback_path: str | Path = ".auth/qwen-storage-state.json",
    strategy: str = "round-robin",
) -> ExecutionAccounts:
    selected_account_id = str(account_id).strip()
    if selected_account_id:
        return ExecutionAccounts(
            strategy="explicit",
            pool_state_path=_pool_state_path(),
            accounts=[resolve_auth_state_path(account_id=selected_account_id, fallback_path=fallback_path)],
        )

    config_path, accounts = load_accounts_config()
    if not accounts:
        return ExecutionAccounts(
            strategy="single-default",
            pool_state_path=_pool_state_path(),
            accounts=[
                ExecutionAccount(
                    account_id="",
                    account_label="",
                    auth_state_path=as_absolute(os.environ.get("QWEN_AUTH_STATE_PATH", fallback_path)),
                    accounts_file_path=config_path,
                )
            ],
        )

    pool_state = _read_pool_state()
    ordered = accounts
    normalized_strategy = normalize_account_strategy(strategy)
    if normalized_strategy == "round-robin":
        ordered = _rotate_accounts(accounts, pool_state["lastSuccessfulAccountId"])
    elif normalized_strategy == "sticky" and pool_state["lastSuccessfulAccountId"]:
        sticky = next((account for account in accounts if account.id == pool_state["lastSuccessfulAccountId"]), None)
        if sticky:
            ordered = [sticky, *[account for account in accounts if account.id != sticky.id]]

    return ExecutionAccounts(
        strategy=normalized_strategy,
        pool_state_path=Path(pool_state["statePath"]),
        accounts=[
            ExecutionAccount(
                account_id=account.id,
                account_label=account.label,
                auth_state_path=as_absolute(account.storage_state_path),
                accounts_file_path=config_path,
            )
            for account in ordered
        ],
    )


def mark_account_success(account_id: str) -> None:
    selected_account_id = str(account_id).strip()
    if not selected_account_id:
        return
    state_path = _pool_state_path()
    ensure_dir(state_path.parent)
    state_path.write_text(
        json.dumps(
            {
                "lastSuccessfulAccountId": selected_account_id,
                "updatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
