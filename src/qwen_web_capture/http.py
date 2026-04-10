from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib import request as urllib_request

from .runtime import ensure_dir


async def api_json(context: Any, url: str, body: Any, headers: dict[str, str] | None = None) -> Any:
    response = await context.fetch(
        url,
        method="POST",
        headers={
            "content-type": "application/json",
            **(headers or {}),
        },
        data=json.dumps(body),
    )
    if not response.ok:
        raise RuntimeError(f"API request failed: {response.status} {response.status_text} {url}")
    return await response.json()


def _download_file(url: str, output_path: Path) -> None:
    req = urllib_request.Request(url, method="GET")
    with urllib_request.urlopen(req) as response:
        data = response.read()
    ensure_dir(output_path.parent)
    output_path.write_bytes(data)


async def download_file(url: str, output_path: str | Path) -> Path:
    path = Path(output_path).resolve()
    await asyncio.to_thread(_download_file, url, path)
    return path
