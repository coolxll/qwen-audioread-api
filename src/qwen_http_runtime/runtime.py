from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import os
import sys


@dataclass(frozen=True, slots=True)
class ExportConfig:
    file_type: int
    extension: str
    label: str


def strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_dotenv(dotenv_path: str | Path | None = None) -> Path:
    path = Path(dotenv_path or Path.cwd() / ".env").resolve()
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = strip_quotes(value.strip())
    except FileNotFoundError:
        pass
    return path


def as_absolute(input_path: str | Path) -> Path:
    path = Path(input_path)
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def ensure_dir(dir_path: str | Path) -> Path:
    path = as_absolute(dir_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def now_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace(":", "-")


def guess_mime_type(file_path: str | Path) -> str:
    suffix = Path(file_path).suffix.lower()
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


def get_export_config(format_name: str) -> ExportConfig:
    normalized = str(format_name).strip().lower()
    if normalized == "docx":
        return ExportConfig(file_type=0, extension=".docx", label="docx")
    if normalized in {"md", "markdown"}:
        return ExportConfig(file_type=3, extension=".md", label="md")
    raise ValueError(f"Unsupported export format: {format_name}")


def enable_live_output() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(line_buffering=True, write_through=True)


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
