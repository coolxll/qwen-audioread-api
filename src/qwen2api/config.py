from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _strip_quotes(value.strip())


def _resolve_path(value: str | Path, *, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    host: str
    port: int
    data_dir: Path
    api_key: str
    runtime_backend: str
    default_format: str
    delete_remote: bool
    export_concurrency: int
    qwen_root: Path
    qwen_dotenv_path: Path
    qwen_auth_state_path: Path
    qwen_accounts_file: Path
    qwen_account_pool_state_file: Path
    qwen_quota_state_file: Path
    keep_job_text: bool
    keep_uploaded_input: bool
    keep_intermediate_outputs: bool
    job_worker_count: int
    max_retries: int
    retry_delay_seconds: int
    retryable_error_codes: tuple[str, ...]

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def runtime_dir(self) -> Path:
        return self.data_dir / "runtime"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def output_name_claims_dir(self) -> Path:
        return self.runtime_dir / "output-name-claims"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.output_name_claims_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    project_root = _project_root()
    _load_dotenv(project_root / ".env")

    data_dir = _resolve_path(os.environ.get("QWEN2API_DATA_DIR", "data"), base_dir=project_root)
    qwen_root = _resolve_path(os.environ.get("QWEN2API_QWEN_ROOT", "."), base_dir=project_root)

    settings = Settings(
        project_root=project_root,
        host=os.environ.get("QWEN2API_HOST", "0.0.0.0"),
        port=int(os.environ.get("QWEN2API_PORT", "8000")),
        data_dir=data_dir,
        api_key=os.environ.get("QWEN2API_API_KEY", "").strip(),
        runtime_backend=os.environ.get("QWEN2API_RUNTIME", "playwright").strip().lower() or "playwright",
        default_format=os.environ.get("QWEN2API_DEFAULT_FORMAT", "md").strip().lower() or "md",
        delete_remote=os.environ.get("QWEN2API_DELETE_REMOTE", "true").strip().lower() in {"1", "true", "yes", "on"},
        export_concurrency=max(1, int(os.environ.get("QWEN2API_EXPORT_CONCURRENCY", "2"))),
        qwen_root=qwen_root,
        qwen_dotenv_path=_resolve_path(os.environ.get("QWEN2API_QWEN_DOTENV", ".env"), base_dir=project_root),
        qwen_auth_state_path=_resolve_path(
            os.environ.get("QWEN2API_QWEN_AUTH_STATE", ".auth/qwen-storage-state.json"),
            base_dir=project_root,
        ),
        qwen_accounts_file=_resolve_path(
            os.environ.get("QWEN2API_QWEN_ACCOUNTS_FILE", "accounts.json"),
            base_dir=project_root,
        ),
        qwen_account_pool_state_file=(data_dir / "runtime" / "account-pool-state.json").resolve(),
        qwen_quota_state_file=(data_dir / "runtime" / "quota-usage.json").resolve(),
        keep_job_text=os.environ.get("QWEN2API_KEEP_JOB_TEXT", "false").strip().lower() in {"1", "true", "yes", "on"},
        keep_uploaded_input=os.environ.get("QWEN2API_KEEP_UPLOADED_INPUT", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        keep_intermediate_outputs=os.environ.get("QWEN2API_KEEP_INTERMEDIATE_OUTPUTS", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        job_worker_count=max(1, int(os.environ.get("QWEN2API_JOB_WORKERS", "2"))),
        max_retries=max(0, int(os.environ.get("QWEN2API_MAX_RETRIES", "2"))),
        retry_delay_seconds=max(0, int(os.environ.get("QWEN2API_RETRY_DELAY_SECONDS", "30"))),
        retryable_error_codes=tuple(
            code.strip()
            for code in os.environ.get(
                "QWEN2API_RETRYABLE_ERROR_CODES",
                "TRANSCRIPTION_TIMEOUT,RATE_LIMITED,TRANSCRIPTION_FAILED",
            ).split(",")
            if code.strip()
        ),
    )
    settings.ensure_directories()
    return settings
