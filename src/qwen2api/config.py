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


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    host: str
    port: int
    data_dir: Path
    api_key: str
    default_format: str
    delete_remote: bool
    export_concurrency: int
    qwen_root: Path
    qwen_dotenv_path: Path
    qwen_auth_state_path: Path
    qwen_accounts_file: Path
    qwen_account_pool_state_file: Path
    qwen_quota_state_file: Path

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def runtime_dir(self) -> Path:
        return self.data_dir / "runtime"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    project_root = _project_root()
    _load_dotenv(project_root / ".env")

    data_dir = Path(os.environ.get("QWEN2API_DATA_DIR", project_root / "data")).expanduser().resolve()
    qwen_root = Path(
        os.environ.get("QWEN2API_QWEN_ROOT", "/Users/gq/Projects/openclaw-qwen-web-capture-skill")
    ).expanduser().resolve()

    settings = Settings(
        project_root=project_root,
        host=os.environ.get("QWEN2API_HOST", "0.0.0.0"),
        port=int(os.environ.get("QWEN2API_PORT", "8000")),
        data_dir=data_dir,
        api_key=os.environ.get("QWEN2API_API_KEY", "").strip(),
        default_format=os.environ.get("QWEN2API_DEFAULT_FORMAT", "md").strip().lower() or "md",
        delete_remote=os.environ.get("QWEN2API_DELETE_REMOTE", "true").strip().lower() in {"1", "true", "yes", "on"},
        export_concurrency=max(1, int(os.environ.get("QWEN2API_EXPORT_CONCURRENCY", "2"))),
        qwen_root=qwen_root,
        qwen_dotenv_path=Path(os.environ.get("QWEN2API_QWEN_DOTENV", qwen_root / ".env")).expanduser().resolve(),
        qwen_auth_state_path=Path(
            os.environ.get("QWEN2API_QWEN_AUTH_STATE", qwen_root / ".auth/qwen-storage-state.json")
        ).expanduser().resolve(),
        qwen_accounts_file=Path(
            os.environ.get("QWEN2API_QWEN_ACCOUNTS_FILE", qwen_root / "accounts.json")
        ).expanduser().resolve(),
        qwen_account_pool_state_file=(data_dir / "runtime" / "account-pool-state.json").resolve(),
        qwen_quota_state_file=(data_dir / "runtime" / "quota-usage.json").resolve(),
    )
    settings.ensure_directories()
    return settings
