"""Tests for minimal auth file format support."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qwen_http_runtime.minimal_auth import (
    is_minimal_auth_format,
    is_legacy_storage_format,
    parse_minimal_auth,
    parse_legacy_storage,
    load_auth_file,
    create_minimal_auth,
    write_minimal_auth,
    convert_legacy_to_minimal,
    MinimalAuth,
)


class MinimalAuthFormatTests(unittest.TestCase):
    """Test minimal auth format detection and validation."""

    def test_is_minimal_auth_format_valid(self) -> None:
        """Test valid minimal auth format."""
        payload = {"tongyi_sso_ticket": "test-cookie-value"}
        self.assertTrue(is_minimal_auth_format(payload))

    def test_is_minimal_auth_format_with_extra_fields(self) -> None:
        """Test minimal format with extra fields (still valid)."""
        payload = {
            "tongyi_sso_ticket": "test-value",
            "extra_field": "ignored",
        }
        self.assertTrue(is_minimal_auth_format(payload))

    def test_is_minimal_auth_format_empty_ticket(self) -> None:
        """Test minimal format with empty ticket (invalid)."""
        payload = {"tongyi_sso_ticket": ""}
        self.assertFalse(is_minimal_auth_format(payload))

    def test_is_minimal_auth_format_whitespace_ticket(self) -> None:
        """Test minimal format with whitespace-only ticket (invalid)."""
        payload = {"tongyi_sso_ticket": "   "}
        self.assertFalse(is_minimal_auth_format(payload))

    def test_is_minimal_auth_format_has_cookies(self) -> None:
        """Test that payload with 'cookies' key is not minimal format."""
        payload = {
            "tongyi_sso_ticket": "test-value",
            "cookies": [],
        }
        self.assertFalse(is_minimal_auth_format(payload))

    def test_is_minimal_auth_format_not_dict(self) -> None:
        """Test non-dict payload."""
        self.assertFalse(is_minimal_auth_format([]))
        self.assertFalse(is_minimal_auth_format("string"))
        self.assertFalse(is_minimal_auth_format(None))

    def test_is_legacy_storage_format_valid(self) -> None:
        """Test valid legacy storage format."""
        payload = {"cookies": []}
        self.assertTrue(is_legacy_storage_format(payload))

    def test_is_legacy_storage_format_with_cookies(self) -> None:
        """Test legacy format with actual cookies."""
        payload = {
            "cookies": [
                {"name": "tongyi_sso_ticket", "value": "test", "domain": ".qianwen.com"}
            ],
            "origins": [],
        }
        self.assertTrue(is_legacy_storage_format(payload))

    def test_is_legacy_storage_format_not_dict(self) -> None:
        """Test non-dict payload."""
        self.assertFalse(is_legacy_storage_format([]))


class ParseMinimalAuthTests(unittest.TestCase):
    """Test parsing minimal auth format."""

    def test_parse_valid_minimal_auth(self) -> None:
        """Test parsing valid minimal auth."""
        payload = {"tongyi_sso_ticket": "cookie-123"}
        result = parse_minimal_auth(payload)
        self.assertIsInstance(result, MinimalAuth)
        self.assertEqual(result.tongyi_sso_ticket, "cookie-123")

    def test_parse_minimal_auth_strips_whitespace(self) -> None:
        """Test that parsing strips whitespace from ticket."""
        payload = {"tongyi_sso_ticket": "  cookie-123  "}
        result = parse_minimal_auth(payload)
        self.assertEqual(result.tongyi_sso_ticket, "cookie-123")

    def test_parse_invalid_minimal_auth(self) -> None:
        """Test parsing invalid minimal auth raises error."""
        with self.assertRaises(ValueError):
            parse_minimal_auth({"cookies": []})


class ParseLegacyStorageTests(unittest.TestCase):
    """Test parsing legacy storage format."""

    def test_parse_valid_legacy_storage(self) -> None:
        """Test parsing valid legacy storage."""
        payload = {
            "cookies": [{"name": "test", "value": "value"}],
            "origins": [],
        }
        result = parse_legacy_storage(payload)
        self.assertEqual(result, payload)

    def test_parse_invalid_legacy_storage(self) -> None:
        """Test parsing invalid legacy storage raises error."""
        with self.assertRaises(ValueError):
            parse_legacy_storage({"tongyi_sso_ticket": "value"})


class LoadAuthFileTests(unittest.TestCase):
    """Test loading auth files."""

    def test_load_minimal_auth_file(self) -> None:
        """Test loading a minimal auth file."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text('{"tongyi_sso_ticket": "test-value"}', encoding="utf-8")
            
            payload = load_auth_file(path)
            self.assertTrue(is_minimal_auth_format(payload))
            self.assertEqual(payload["tongyi_sso_ticket"], "test-value")

    def test_load_legacy_auth_file(self) -> None:
        """Test loading a legacy auth file."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text('{"cookies": []}', encoding="utf-8")
            
            payload = load_auth_file(path)
            self.assertTrue(is_legacy_storage_format(payload))

    def test_load_nonexistent_file(self) -> None:
        """Test loading non-existent file raises error."""
        with self.assertRaises(FileNotFoundError):
            load_auth_file("/nonexistent/path/auth.json")

    def test_load_invalid_json(self) -> None:
        """Test loading invalid JSON raises error."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text("not valid json", encoding="utf-8")
            
            with self.assertRaises(ValueError):
                load_auth_file(path)

    def test_load_non_object_json(self) -> None:
        """Test loading JSON array raises error."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text("[]", encoding="utf-8")
            
            with self.assertRaises(ValueError):
                load_auth_file(path)


class CreateMinimalAuthTests(unittest.TestCase):
    """Test creating minimal auth content."""

    def test_create_minimal_auth(self) -> None:
        """Test creating minimal auth dict."""
        result = create_minimal_auth("test-ticket")
        self.assertEqual(result, {"tongyi_sso_ticket": "test-ticket"})

    def test_create_minimal_auth_strips_whitespace(self) -> None:
        """Test that creating strips whitespace."""
        result = create_minimal_auth("  test-ticket  ")
        self.assertEqual(result, {"tongyi_sso_ticket": "test-ticket"})

    def test_write_minimal_auth(self) -> None:
        """Test writing minimal auth file."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subdir" / "auth.json"
            write_minimal_auth(path, "test-ticket")
            
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["tongyi_sso_ticket"], "test-ticket")


class ConvertLegacyToMinimalTests(unittest.TestCase):
    """Test converting legacy format to minimal."""

    def test_convert_with_ticket_cookie(self) -> None:
        """Test converting legacy format with ticket cookie."""
        legacy = {
            "cookies": [
                {"name": "tongyi_sso_ticket", "value": "abc123", "domain": ".qianwen.com"},
                {"name": "other", "value": "ignored", "domain": ".example.com"},
            ]
        }
        result = convert_legacy_to_minimal(legacy)
        self.assertIsNotNone(result)
        self.assertEqual(result, {"tongyi_sso_ticket": "abc123"})

    def test_convert_without_ticket_cookie(self) -> None:
        """Test converting legacy format without ticket cookie returns None."""
        legacy = {
            "cookies": [
                {"name": "other", "value": "value", "domain": ".example.com"},
            ]
        }
        result = convert_legacy_to_minimal(legacy)
        self.assertIsNone(result)

    def test_convert_empty_cookies(self) -> None:
        """Test converting legacy format with empty cookies."""
        legacy = {"cookies": []}
        result = convert_legacy_to_minimal(legacy)
        self.assertIsNone(result)

    def test_convert_non_legacy(self) -> None:
        """Test converting non-legacy format returns None."""
        legacy = {"tongyi_sso_ticket": "value"}
        result = convert_legacy_to_minimal(legacy)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
