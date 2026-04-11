"""Lightweight authentication file format support.

Current format requirements:
- Minimal: just the cookie value
  ```json
  {
    "tongyi_sso_ticket": "actual-cookie-value-here"
  }
  ```

- Legacy compatible: full Playwright storage state
  ```json
  {
    "cookies": [...],
    "origins": [...]
  }
  ```

Runtime priority:
1. Try to read minimal format first (faster, cleaner)
2. Fall back to legacy storage-state format
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any


@dataclass(frozen=True, slots=True)
class MinimalAuth:
    """Minimal authentication payload containing only the essential cookie."""
    tongyi_sso_ticket: str


def is_minimal_auth_format(payload: dict[str, Any]) -> bool:
    """Check if the payload matches the minimal auth format.

    Minimal format requires:
    - Has 'tongyi_sso_ticket' key with a non-empty string value
    - Does NOT have 'cookies' key (which indicates legacy format)
    """
    if not isinstance(payload, dict):
        return False
    if "cookies" in payload:
        return False
    ticket = payload.get("tongyi_sso_ticket")
    return isinstance(ticket, str) and ticket.strip() != ""


def is_legacy_storage_format(payload: dict[str, Any]) -> bool:
    """Check if the payload matches the legacy Playwright storage-state format."""
    if not isinstance(payload, dict):
        return False
    return "cookies" in payload


def parse_minimal_auth(payload: dict[str, Any]) -> MinimalAuth:
    """Parse and validate minimal auth format."""
    if not is_minimal_auth_format(payload):
        raise ValueError("Payload does not match minimal auth format")
    return MinimalAuth(tongyi_sso_ticket=str(payload["tongyi_sso_ticket"]).strip())


def parse_legacy_storage(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse and validate legacy storage-state format."""
    if not is_legacy_storage_format(payload):
        raise ValueError("Payload does not match legacy storage format")
    return payload


def load_auth_file(auth_path: str | Path) -> dict[str, Any]:
    """Load and parse an authentication file.

    Supports both minimal and legacy formats.
    Returns the raw parsed JSON for downstream processing.
    """
    path = Path(auth_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Auth file not found: {path}")
    
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in auth file {path}: {error}") from error
    
    if not isinstance(payload, dict):
        raise ValueError("Auth file must contain a JSON object")
    
    return payload


def create_minimal_auth(ticket_value: str) -> dict[str, str]:
    """Create a minimal auth file content."""
    return {
        "tongyi_sso_ticket": ticket_value.strip(),
    }


def write_minimal_auth(auth_path: str | Path, ticket_value: str) -> Path:
    """Write a minimal auth file."""
    path = Path(auth_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = create_minimal_auth(ticket_value)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def convert_legacy_to_minimal(legacy_payload: dict[str, Any]) -> dict[str, str] | None:
    """Convert legacy storage-state to minimal auth format.

    Extracts tongyi_sso_ticket from the cookies array.
    Returns None if the cookie is not found.
    """
    if not is_legacy_storage_format(legacy_payload):
        return None
    
    cookies = legacy_payload.get("cookies", [])
    for cookie in cookies:
        if isinstance(cookie, dict) and cookie.get("name") == "tongyi_sso_ticket":
            return create_minimal_auth(str(cookie.get("value", "")))
    
    return None
