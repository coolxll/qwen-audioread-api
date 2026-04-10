from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def metadata_sidecar_path(transcript_path: str | Path) -> Path:
    path = Path(transcript_path)
    return path.with_suffix(path.suffix + ".meta.json")


def write_result_metadata(transcript_path: str | Path, payload: dict[str, Any]) -> Path:
    sidecar = metadata_sidecar_path(transcript_path)
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar


def read_result_metadata(transcript_path: str | Path) -> dict[str, Any]:
    sidecar = metadata_sidecar_path(transcript_path)
    if not sidecar.exists():
        return {}
    try:
        parsed = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
