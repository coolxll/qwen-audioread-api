from __future__ import annotations

import asyncio
from pathlib import Path
from urllib import request as urllib_request

from .runtime import ensure_dir


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
