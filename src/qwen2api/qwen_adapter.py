from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
import os
import sys
from typing import Any

from .config import Settings


@dataclass(frozen=True, slots=True)
class NativeFlowResult:
    export_path: Path
    record_id: str
    gen_record_id: str
    remote_deleted: bool
    account_id: str
    account_label: str


@dataclass(frozen=True, slots=True)
class QwenBundle:
    load_dotenv: Any
    get_export_config: Any
    resolve_execution_accounts: Any
    normalize_account_strategy: Any
    mark_account_success: Any
    run_real_flow: Any
    write_result_metadata: Any


def _import_with_optional_src(settings: Settings, module_name: str):
    try:
        return import_module(module_name)
    except ModuleNotFoundError:
        src_dir = settings.qwen_root / "src"
        if not src_dir.exists():
            raise
        src_entry = str(src_dir)
        if src_entry not in sys.path:
            sys.path.insert(0, src_entry)
        return import_module(module_name)


def load_qwen_bundle(settings: Settings) -> QwenBundle:
    if settings.runtime_backend == "http":
        runtime = import_module("qwen_http_runtime.runtime")
        accounts = import_module("qwen_http_runtime.accounts")
        flow = import_module("qwen_http_runtime.flow")
        result_metadata = import_module("qwen_http_runtime.result_metadata")
    else:
        runtime = _import_with_optional_src(settings, "qwen_web_capture.runtime")
        accounts = _import_with_optional_src(settings, "qwen_web_capture.accounts")
        flow = _import_with_optional_src(settings, "qwen_web_capture.flow")
        result_metadata = _import_with_optional_src(settings, "qwen_web_capture.result_metadata")
    return QwenBundle(
        load_dotenv=runtime.load_dotenv,
        get_export_config=runtime.get_export_config,
        resolve_execution_accounts=accounts.resolve_execution_accounts,
        normalize_account_strategy=accounts.normalize_account_strategy,
        mark_account_success=accounts.mark_account_success,
        run_real_flow=flow.run_real_flow,
        write_result_metadata=result_metadata.write_result_metadata,
    )


def configure_qwen_environment(settings: Settings) -> None:
    os.environ["QWEN_AUTH_STATE_PATH"] = str(settings.qwen_auth_state_path)
    os.environ["QWEN_ACCOUNTS_FILE"] = str(settings.qwen_accounts_file)
    os.environ["QWEN_ACCOUNT_POOL_STATE_FILE"] = str(settings.qwen_account_pool_state_file)
    os.environ["QWEN_QUOTA_STATE_FILE"] = str(settings.qwen_quota_state_file)
    os.environ["QWEN_EXPORT_CONCURRENCY"] = str(settings.export_concurrency)


async def transcribe_via_qwen(
    *,
    settings: Settings,
    input_path: Path,
    output_dir: Path,
    export_format: str,
    delete_remote: bool,
    account_id: str,
    account_strategy: str,
) -> NativeFlowResult:
    configure_qwen_environment(settings)
    bundle = load_qwen_bundle(settings)
    bundle.load_dotenv(settings.qwen_dotenv_path)

    export_config = bundle.get_export_config(export_format)
    strategy = bundle.normalize_account_strategy(account_strategy)
    execution = bundle.resolve_execution_accounts(
        account_id=account_id,
        fallback_path=settings.qwen_auth_state_path,
        strategy=strategy,
    )
    export_gate = asyncio.Semaphore(settings.export_concurrency)

    last_error: Exception | None = None
    for account in execution.accounts:
        try:
            result = await bundle.run_real_flow(
                file_path=input_path,
                auth_state_path=account.auth_state_path,
                download_dir=output_dir,
                export_config=export_config,
                should_delete=delete_remote,
                account_id=account.account_id,
                export_gate=export_gate,
            )
            bundle.write_result_metadata(
                result.export_path,
                {
                    "record_id": result.record_id,
                    "gen_record_id": result.gen_record_id,
                    "remote_deleted": result.remote_deleted,
                    "remote_delete_status": "success" if result.remote_deleted else "failed",
                },
            )
            bundle.mark_account_success(account.account_id)
            return NativeFlowResult(
                export_path=Path(result.export_path).resolve(),
                record_id=result.record_id,
                gen_record_id=result.gen_record_id,
                remote_deleted=result.remote_deleted,
                account_id=account.account_id,
                account_label=account.account_label,
            )
        except Exception as error:  # noqa: BLE001
            last_error = error
            if len(execution.accounts) == 1:
                break

    assert last_error is not None
    raise last_error
