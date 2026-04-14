"""Service layer tests for HTTP runtime backend.

These tests cover the real job execution path when runtime_backend=http,
using mocks to simulate the external Qwen API and OSS interactions.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from qwen2api.config import Settings, get_settings
from qwen2api.job_queue import build_retry_payload, recover_pending_jobs, retry_wait_seconds, should_retry_job
from qwen2api.maintenance import cleanup_jobs, collect_failed_retry_candidates
from qwen2api.qwen_adapter import NativeFlowResult, load_qwen_bundle
from qwen2api.service import build_success_payload, serialize_job_payload
from qwen2api.storage import (
    batch_file,
    init_job_payload,
    job_dir,
    load_job,
    release_markdown_name_reservation,
    reserve_markdown_name,
    save_batch,
    save_job,
)


def make_http_settings(root: Path) -> Settings:
    """Create Settings configured for HTTP runtime backend."""
    data_dir = root / "data"
    settings = Settings(
        project_root=root,
        host="127.0.0.1",
        port=18000,
        data_dir=data_dir,
        api_key="",
        runtime_backend="http",
        default_format="md",
        delete_remote=True,
        export_concurrency=2,
        qwen_root=root,
        qwen_dotenv_path=root / ".env",
        qwen_auth_state_path=root / "auth-state.json",
        qwen_accounts_file=root / "accounts.json",
        qwen_account_pool_state_file=data_dir / "runtime" / "account-pool-state.json",
        qwen_quota_state_file=data_dir / "runtime" / "quota-usage.json",
        keep_job_text=False,
        keep_uploaded_input=False,
        keep_intermediate_outputs=False,
        job_worker_count=1,
        max_retries=2,
        retry_delay_seconds=1,
        retryable_error_codes=("TRANSCRIPTION_TIMEOUT", "RATE_LIMITED", "TRANSCRIPTION_FAILED"),
    )
    settings.ensure_directories()
    return settings


class HttpRuntimeBundleTests(unittest.TestCase):
    """Test HTTP runtime bundle loading and module resolution."""

    def test_load_qwen_bundle_uses_http_modules(self) -> None:
        """Verify that HTTP runtime loads its own modules."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_http_settings(root)
            bundle = load_qwen_bundle(settings)

            self.assertEqual(bundle.load_dotenv.__module__, "qwen_http_runtime.runtime")
            self.assertEqual(bundle.get_export_config.__module__, "qwen_http_runtime.runtime")
            self.assertEqual(bundle.resolve_execution_accounts.__module__, "qwen_http_runtime.accounts")
            self.assertEqual(bundle.normalize_account_strategy.__module__, "qwen_http_runtime.accounts")
            self.assertEqual(bundle.mark_account_success.__module__, "qwen_http_runtime.accounts")
            self.assertEqual(bundle.run_real_flow.__module__, "qwen_http_runtime.flow")
            self.assertEqual(bundle.write_result_metadata.__module__, "qwen_http_runtime.result_metadata")

    def test_load_qwen_bundle_export_config_md(self) -> None:
        """Test export config for markdown format."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_http_settings(root)
            bundle = load_qwen_bundle(settings)

            config = bundle.get_export_config("md")
            self.assertEqual(config.file_type, 3)
            self.assertEqual(config.extension, ".md")
            self.assertEqual(config.label, "md")

    def test_load_qwen_bundle_export_config_markdown(self) -> None:
        """Test export config for markdown format (alternative name)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_http_settings(root)
            bundle = load_qwen_bundle(settings)

            config = bundle.get_export_config("markdown")
            self.assertEqual(config.file_type, 3)
            self.assertEqual(config.extension, ".md")
            self.assertEqual(config.label, "md")

    def test_load_qwen_bundle_export_config_docx(self) -> None:
        """Test export config for docx format."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_http_settings(root)
            bundle = load_qwen_bundle(settings)

            config = bundle.get_export_config("docx")
            self.assertEqual(config.file_type, 0)
            self.assertEqual(config.extension, ".docx")
            self.assertEqual(config.label, "docx")


class HttpRuntimeUploadTests(unittest.TestCase):
    """Test HTTP runtime upload helpers."""

    def test_upload_file_to_oss_streams_chunks_from_path(self) -> None:
        """Multipart upload should stream a path without preloading the whole file."""
        from qwen_http_runtime.oss_upload import upload_file_to_oss

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.mp4"
            source.write_bytes(b"abcdefghij")

            token = {
                "sts": {
                    "bucket": "bucket",
                    "endpoint": "oss.example.com",
                    "fileKey": "uploads/sample.mp4",
                    "accessKeyId": "key-id",
                    "accessKeySecret": "secret",
                    "securityToken": "security-token",
                }
            }

            chunks: list[bytes] = []

            def record_part(sts, upload_id, part_number, chunk, mime_type):
                chunks.append(chunk)
                return f"etag-{part_number}"

            with patch("qwen_http_runtime.oss_upload.initiate_multipart_upload", return_value="upload-1"), patch(
                "qwen_http_runtime.oss_upload.upload_part",
                side_effect=record_part,
            ), patch("qwen_http_runtime.oss_upload.complete_multipart_upload", return_value=None):
                asyncio.run(
                    upload_file_to_oss(
                        token=token,
                        file_buffer=source,
                        mime_type="video/mp4",
                        part_size=4,
                        upload_mode="multipart",
                    )
                )

            self.assertEqual([len(chunk) for chunk in chunks], [4, 4, 2])
            self.assertEqual(b"".join(chunks), b"abcdefghij")


class HttpRuntimeServiceExecutionTests(unittest.TestCase):
    """Test service layer execution with HTTP runtime backend."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.settings = make_http_settings(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_build_success_payload_http_runtime(self) -> None:
        """Test build_success_payload with HTTP runtime NativeFlowResult."""
        source_dir = self.root / "job" / "outputs"
        source_dir.mkdir(parents=True, exist_ok=True)
        export_path = source_dir / "test_output.md"
        export_path.write_text("# Test Content\n", encoding="utf-8")

        job_payload = init_job_payload(
            "job_http_test",
            original_filename="test_video.mp4",
            delete_remote=True,
            account_id="",
            account_strategy="round-robin",
            queued=False,
            suggested_poll_seconds=60,
            target_markdown_name="test_output.md",
        )

        flow_result = NativeFlowResult(
            export_path=export_path,
            record_id="http-record-123",
            gen_record_id="http-gen-456",
            remote_deleted=True,
            account_id="acc-http-1",
            account_label="HTTP Account 1",
        )

        result = build_success_payload(
            settings=self.settings,
            job_payload=job_payload,
            flow_result=flow_result,
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertIn("test_output", result["markdown_filename"])
        self.assertEqual(result["record_id"], "http-record-123")
        self.assertEqual(result["gen_record_id"], "http-gen-456")
        self.assertTrue(result["remote_deleted"])
        self.assertEqual(result["account_id"], "acc-http-1")
        self.assertEqual(result["account_label"], "HTTP Account 1")

    def test_build_success_payload_output_collision_handling(self) -> None:
        """Test that output collision is handled correctly with HTTP runtime."""
        existing_output = self.settings.outputs_dir / "collision_test.md"
        existing_output.write_text("old content\n", encoding="utf-8")

        source_dir = self.root / "job" / "outputs"
        source_dir.mkdir(parents=True, exist_ok=True)
        export_path = source_dir / "collision_test.md"
        export_path.write_text("new content\n", encoding="utf-8")

        job_payload = init_job_payload(
            "job_collision",
            original_filename="collision_video.mp4",
            delete_remote=True,
            account_id="",
            account_strategy="round-robin",
            queued=False,
            suggested_poll_seconds=60,
            target_markdown_name="collision_test.md",
        )

        flow_result = NativeFlowResult(
            export_path=export_path,
            record_id="record-collision",
            gen_record_id="gen-collision",
            remote_deleted=True,
            account_id="",
            account_label="",
        )

        result = build_success_payload(
            settings=self.settings,
            job_payload=job_payload,
            flow_result=flow_result,
        )

        self.assertEqual(result["markdown_filename"], "collision_test-2.md")
        self.assertEqual(Path(result["output_file"]).name, "collision_test-2.md")
        self.assertEqual(existing_output.read_text(encoding="utf-8"), "old content\n")

    def test_serialize_job_payload_drops_text_http_runtime(self) -> None:
        """Test that text is dropped by default in HTTP runtime serialization."""
        payload = {
            "job_id": "job-http-serialize",
            "status": "succeeded",
            "format": "md",
            "text": "# HTTP Runtime Content\n",
            "meta": {
                "account_id": "acc-http",
                "account_label": "HTTP Account",
            },
        }
        stored = serialize_job_payload(self.settings, payload)
        self.assertIsNone(stored["text"])
        self.assertEqual(stored["job_id"], "job-http-serialize")
        self.assertEqual(stored["status"], "succeeded")

    def test_serialize_job_payload_keeps_text_when_configured(self) -> None:
        """Test that text is kept when keep_job_text is enabled."""
        settings_with_text = Settings(
            project_root=self.root,
            host="127.0.0.1",
            port=18000,
            data_dir=self.root / "data",
            api_key="",
            runtime_backend="http",
            default_format="md",
            delete_remote=True,
            export_concurrency=2,
            qwen_root=self.root,
            qwen_dotenv_path=self.root / ".env",
            qwen_auth_state_path=self.root / "auth-state.json",
            qwen_accounts_file=self.root / "accounts.json",
            qwen_account_pool_state_file=self.root / "data" / "runtime" / "account-pool-state.json",
            qwen_quota_state_file=self.root / "data" / "runtime" / "quota-usage.json",
            keep_job_text=True,
            keep_uploaded_input=False,
            keep_intermediate_outputs=False,
            job_worker_count=1,
            max_retries=2,
            retry_delay_seconds=1,
            retryable_error_codes=("TRANSCRIPTION_TIMEOUT", "RATE_LIMITED", "TRANSCRIPTION_FAILED"),
        )

        payload = {
            "job_id": "job-http-keep-text",
            "status": "succeeded",
            "format": "md",
            "text": "# Keep This Text\n",
            "meta": {},
        }
        stored = serialize_job_payload(settings_with_text, payload)
        self.assertEqual(stored["text"], "# Keep This Text\n")


class HttpRuntimeJobQueueTests(unittest.TestCase):
    """Test job queue operations with HTTP runtime."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.settings = make_http_settings(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_should_retry_job_http_runtime(self) -> None:
        """Test retry decision for HTTP runtime jobs."""
        failed_payload = init_job_payload(
            "job_http_retry",
            original_filename="retry_video.mp4",
            delete_remote=True,
            account_id="",
            account_strategy="round-robin",
            queued=False,
        )
        failed_payload.update(
            {
                "status": "failed",
                "error": {"code": "TRANSCRIPTION_TIMEOUT", "message": "Request timed out"},
            }
        )

        self.assertTrue(should_retry_job(self.settings, failed_payload))

    def test_should_not_retry_non_retryable_error_http_runtime(self) -> None:
        """Test that non-retryable errors are not retried."""
        failed_payload = init_job_payload(
            "job_http_no_retry",
            original_filename="fail_video.mp4",
            delete_remote=True,
            account_id="",
            account_strategy="round-robin",
            queued=False,
        )
        failed_payload.update(
            {
                "status": "failed",
                "error": {"code": "INVALID_FORMAT", "message": "Unsupported format"},
            }
        )

        self.assertFalse(should_retry_job(self.settings, failed_payload))

    def test_build_retry_payload_http_runtime(self) -> None:
        """Test building retry payload for HTTP runtime jobs."""
        failed_payload = init_job_payload(
            "job_http_retry_build",
            original_filename="retry_video.mp4",
            delete_remote=True,
            account_id="",
            account_strategy="round-robin",
            queued=False,
        )
        failed_payload.update(
            {
                "status": "failed",
                "error": {"code": "TRANSCRIPTION_TIMEOUT", "message": "timed out"},
            }
        )

        retry_payload = build_retry_payload(self.settings, failed_payload)
        self.assertEqual(retry_payload["status"], "queued")
        self.assertEqual(retry_payload["meta"]["retry_count"], 1)

    def test_retry_exhausted_http_runtime(self) -> None:
        """Test that jobs with max retries are not retried."""
        failed_payload = init_job_payload(
            "job_http_exhausted",
            original_filename="retry_video.mp4",
            delete_remote=True,
            account_id="",
            account_strategy="round-robin",
            queued=False,
        )
        failed_payload.update(
            {
                "status": "failed",
                "error": {"code": "TRANSCRIPTION_TIMEOUT", "message": "timed out"},
                "meta": {
                    "retry_count": self.settings.max_retries,
                    "source_mode": "upload",
                },
            }
        )

        self.assertFalse(should_retry_job(self.settings, failed_payload))

    def test_retry_wait_seconds_http_runtime(self) -> None:
        """Test retry delay calculation for HTTP runtime."""
        payload = init_job_payload(
            "job_http_retry_delay",
            original_filename="video.mp4",
            delete_remote=True,
            account_id="",
            account_strategy="round-robin",
            queued=True,
        )
        payload["meta"]["retry_count"] = 1
        wait = retry_wait_seconds(payload)
        self.assertGreaterEqual(wait, 0)


class HttpRuntimeMaintenanceTests(unittest.TestCase):
    """Test maintenance operations with HTTP runtime."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.settings = make_http_settings(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_cleanup_jobs_http_runtime(self) -> None:
        """Test cleanup jobs created by HTTP runtime."""
        old_job_dir = job_dir(self.settings.jobs_dir, "job_old")
        old_input = old_job_dir / "old.mp4"
        old_input.write_bytes(b"old")
        old_payload = init_job_payload(
            "job_old",
            original_filename="old.mp4",
            delete_remote=True,
            account_id="",
            account_strategy="round-robin",
            queued=True,
        )
        old_payload.update(
            {
                "status": "failed",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:00+00:00",
                "error": {"code": "TRANSCRIPTION_FAILED", "message": "boom"},
            }
        )
        old_payload["meta"]["job_dir"] = str(old_job_dir)
        old_payload["meta"]["input_file"] = str(old_input)
        old_payload["meta"]["file_size_bytes"] = 50 * 1024 * 1024
        old_payload["meta"]["source_mode"] = "local_path"
        save_job(self.settings.jobs_dir, "job_old", old_payload)

        applied = cleanup_jobs(self.settings, older_than_hours=1, statuses={"failed"}, dry_run=False)
        self.assertIn("job_old", applied["removed_jobs"])
        self.assertFalse((self.settings.jobs_dir / "job_old").exists())

    def test_collect_failed_retry_candidates_http_runtime(self) -> None:
        """Test collecting retry candidates for HTTP runtime batch."""
        old_job_dir = job_dir(self.settings.jobs_dir, "job_old")
        old_input = old_job_dir / "old.mp4"
        old_input.write_bytes(b"old")
        old_payload = init_job_payload(
            "job_old",
            original_filename="old.mp4",
            delete_remote=True,
            account_id="",
            account_strategy="round-robin",
            queued=False,
        )
        old_payload.update(
            {
                "status": "failed",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:00+00:00",
                "error": {"code": "TRANSCRIPTION_TIMEOUT", "message": "timed out"},
            }
        )
        old_payload["meta"]["job_dir"] = str(old_job_dir)
        old_payload["meta"]["input_file"] = str(old_input)
        old_payload["meta"]["file_size_bytes"] = 50 * 1024 * 1024
        old_payload["meta"]["source_mode"] = "local_path"
        old_payload["meta"]["source_path"] = str(old_input)
        save_job(self.settings.jobs_dir, "job_old", old_payload)

        batch_payload = {
            "batch_id": "batch_old",
            "total": 1,
            "accepted": 1,
            "format": "md",
            "output_dir": str(self.settings.outputs_dir),
            "items": [
                {
                    "job_id": "job_old",
                    "original_filename": "old.mp4",
                    "markdown_filename": "old.md",
                    "status": "failed",
                    "job_url": "/api/v1/jobs/job_old",
                    "download_url": "/api/v1/jobs/job_old/file",
                    "suggested_poll_after_seconds": 60,
                }
            ],
        }
        save_batch(self.settings.runtime_dir, "batch_old", batch_payload)

        retries = collect_failed_retry_candidates(self.settings, "batch_old")
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0].retry_path, str(old_input))


class HttpRuntimeReportingTests(unittest.TestCase):
    """Test reporting with HTTP runtime."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.settings = make_http_settings(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_batch_report_http_runtime(self) -> None:
        """Test batch report generation for HTTP runtime jobs."""
        from qwen2api.reporting import build_batch_report, render_batch_report_markdown

        output_path = self.settings.outputs_dir / "http_job_output.md"
        output_path.write_text("# HTTP Runtime Output\n", encoding="utf-8")

        job_payload = init_job_payload(
            "job_http_report",
            original_filename="http_video.mp4",
            delete_remote=True,
            account_id="acc-http-1",
            account_strategy="round-robin",
            queued=True,
        )
        job_payload.update(
            {
                "status": "succeeded",
                "output_file": str(output_path),
                "download_url": "/api/v1/jobs/job_http_report/file",
                "completed_at": "2026-04-10T00:01:00+00:00",
                "updated_at": "2026-04-10T00:01:00+00:00",
            }
        )
        job_payload["created_at"] = "2026-04-10T00:00:00+00:00"
        job_payload["meta"]["file_size_bytes"] = 10 * 1024 * 1024
        job_payload["meta"]["source_mode"] = "local_path"
        job_payload["meta"]["account_id"] = "acc-http-1"
        job_payload["meta"]["account_label"] = "HTTP Account 1"
        save_job(self.settings.jobs_dir, "job_http_report", job_payload)

        save_batch(
            self.settings.runtime_dir,
            "batch_http",
            {
                "batch_id": "batch_http",
                "total": 1,
                "accepted": 1,
                "format": "md",
                "output_dir": str(self.settings.outputs_dir),
                "items": [
                    {
                        "job_id": "job_http_report",
                        "original_filename": "http_video.mp4",
                        "markdown_filename": "http_job_output.md",
                        "status": "queued",
                        "job_url": "/api/v1/jobs/job_http_report",
                        "download_url": "/api/v1/jobs/job_http_report/file",
                        "suggested_poll_after_seconds": 60,
                    }
                ],
            },
        )

        report = build_batch_report(self.settings, "batch_http")
        self.assertEqual(report["counts"]["succeeded"], 1)
        self.assertEqual(report["rates"]["success_rate_percent"], 100.0)
        self.assertEqual(report["source_mode_groups"]["local_path"], 1)

        markdown = render_batch_report_markdown(report)
        self.assertIn("Batch Report", markdown)
        self.assertIn("http_job_output.md", markdown)
        self.assertIn("Success rate", markdown)


if __name__ == "__main__":
    unittest.main()
