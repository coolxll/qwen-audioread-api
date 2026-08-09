from __future__ import annotations

import asyncio
from dataclasses import replace
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qwen2api.config import Settings, get_settings
from qwen2api.job_queue import build_retry_payload, recover_pending_jobs, retry_wait_seconds, should_retry_job
from qwen2api.maintenance import cleanup_jobs, collect_failed_retry_candidates
from qwen2api.main import create_app
from qwen2api.qwen_adapter import NativeFlowResult
from qwen2api.reporting import build_batch_report, render_batch_report_markdown
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


def make_settings(root: Path) -> Settings:
    data_dir = root / "data"
    settings = Settings(
        project_root=root,
        host="127.0.0.1",
        port=18000,
        data_dir=data_dir,
        api_key="",
        runtime_backend="playwright",
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


class TranscriptionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.settings = make_settings(self.root)
        app = create_app(self.settings)
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.tempdir.cleanup()

    def test_readiness_reports_missing_auth(self) -> None:
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready", "reason": "auth_missing"})

    def test_readiness_accepts_minimal_auth(self) -> None:
        self.settings.qwen_auth_state_path.write_text(
            '{"tongyi_sso_ticket":"ticket-value"}',
            encoding="utf-8",
        )
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    def test_sync_transcription_returns_markdown_metadata(self) -> None:
        async def fake_run_transcription(**kwargs):
            job_payload = kwargs["job_payload"]
            output_path = self.settings.outputs_dir / job_payload["markdown_filename"]
            output_path.write_text("# demo\n", encoding="utf-8")
            return {
                **job_payload,
                "status": "succeeded",
                "text": "# demo\n",
                "content_type": "text/markdown",
                "output_file": str(output_path),
                "download_url": f"/api/v1/jobs/{job_payload['job_id']}/file",
                "record_id": "record-1",
                "gen_record_id": "gen-1",
                "remote_deleted": True,
                "updated_at": job_payload["updated_at"],
                "completed_at": job_payload["updated_at"],
            }

        with patch("qwen2api.api.transcriptions.run_transcription", new=fake_run_transcription):
            response = self.client.post(
                "/api/v1/transcriptions",
                data={"format": "md"},
                files={"file": ("课程?.mp4", b"video-bytes", "video/mp4")},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["format"], "md")
        self.assertEqual(body["markdown_filename"], "课程？.md")
        self.assertEqual(body["suggested_poll_after_seconds"], 60)

    def test_async_transcription_returns_queue_metadata(self) -> None:
        with patch("qwen2api.api.transcriptions.enqueue_job", new=AsyncMock(return_value=None)):
            response = self.client.post(
                "/api/v1/transcriptions/async",
                data={"format": "md"},
                files={"file": ("课程?.mp4", b"video-bytes", "video/mp4")},
            )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["markdown_filename"], "课程？.md")
        self.assertEqual(body["suggested_poll_after_seconds"], 60)

    def test_local_batch_submission_uses_source_paths_and_returns_unique_names(self) -> None:
        left_dir = self.root / "left"
        right_dir = self.root / "right"
        left_dir.mkdir()
        right_dir.mkdir()
        left_file = left_dir / "课程？.mp4"
        right_file = right_dir / "课程？.mp4"
        left_file.write_bytes(b"a")
        right_file.write_bytes(b"b")

        with patch("qwen2api.api.transcriptions.enqueue_job", new=AsyncMock(return_value=None)):
            response = self.client.post(
                "/api/v1/transcriptions/local/batch",
                json={
                    "paths": [str(left_file), str(right_file)],
                    "format": "md",
                },
            )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["items"][0]["markdown_filename"], "课程？.md")
        self.assertEqual(body["items"][1]["markdown_filename"], "课程？-2.md")

        first_job_id = body["items"][0]["job_id"]
        first_job = self.client.get(f"/api/v1/jobs/{first_job_id}").json()
        self.assertEqual(first_job["meta"]["source_mode"], "local_path")
        self.assertEqual(first_job["meta"]["source_path"], str(left_file.resolve()))
        self.assertFalse(first_job["meta"]["delete_input_after_success"])

    def test_non_md_format_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/transcriptions",
            data={"format": "docx"},
            files={"file": ("demo.mp4", b"video-bytes", "video/mp4")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "MD_ONLY_OUTPUT")

    def test_batch_report_endpoint_returns_markdown(self) -> None:
        output_path = self.settings.outputs_dir / "课程？.md"
        output_path.write_text("# done\n", encoding="utf-8")
        job_payload = init_job_payload(
            "job_report",
            original_filename="课程?.mp4",
            delete_remote=True,
            account_id="",
            account_strategy="round-robin",
            queued=True,
        )
        job_payload.update(
            {
                "status": "succeeded",
                "output_file": str(output_path),
                "download_url": "/api/v1/jobs/job_report/file",
                "completed_at": "2026-04-10T00:01:00+00:00",
                "updated_at": "2026-04-10T00:01:00+00:00",
            }
        )
        job_payload["created_at"] = "2026-04-10T00:00:00+00:00"
        job_payload["meta"]["file_size_bytes"] = 10 * 1024 * 1024
        job_payload["meta"]["source_mode"] = "local_path"
        save_job(self.settings.jobs_dir, "job_report", job_payload)
        save_batch(
            self.settings.runtime_dir,
            "batch_report",
            {
                "batch_id": "batch_report",
                "total": 1,
                "accepted": 1,
                "format": "md",
                "output_dir": str(self.settings.outputs_dir),
                "items": [
                    {
                        "job_id": "job_report",
                        "original_filename": "课程?.mp4",
                        "markdown_filename": "课程？.md",
                        "status": "queued",
                        "job_url": "/api/v1/jobs/job_report",
                        "download_url": "/api/v1/jobs/job_report/file",
                        "suggested_poll_after_seconds": 60,
                    }
                ],
            },
        )

        response = self.client.get("/api/v1/batches/batch_report/report?format=md")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Batch Report", response.text)
        self.assertIn("课程？.md", response.text)
        self.assertIn("Success rate", response.text)
        self.assertIn("Source Modes", response.text)


class ServiceTests(unittest.TestCase):
    def test_get_settings_uses_repo_relative_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "QWEN2API_DATA_DIR=./custom-data",
                        "QWEN2API_QWEN_ROOT=.",
                        "QWEN2API_QWEN_DOTENV=.env",
                        "QWEN2API_QWEN_AUTH_STATE=.auth/qwen-storage-state.json",
                        "QWEN2API_QWEN_ACCOUNTS_FILE=accounts.json",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                get_settings.cache_clear()
                with patch("qwen2api.config._project_root", return_value=root):
                    settings = get_settings()

            self.assertEqual(settings.project_root, root)
            self.assertEqual(settings.data_dir, (root / "custom-data").resolve())
            self.assertEqual(settings.runtime_backend, "http")
            self.assertEqual(settings.qwen_root, root.resolve())
            self.assertEqual(settings.qwen_dotenv_path, (root / ".env").resolve())
            self.assertEqual(settings.qwen_auth_state_path, (root / ".auth" / "qwen-storage-state.json").resolve())
            self.assertEqual(settings.qwen_accounts_file, (root / "accounts.json").resolve())

            get_settings.cache_clear()

    def test_get_settings_runtime_override_keeps_playwright_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("QWEN2API_RUNTIME=playwright\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                get_settings.cache_clear()
                with patch("qwen2api.config._project_root", return_value=root):
                    settings = get_settings()

            self.assertEqual(settings.runtime_backend, "playwright")
            get_settings.cache_clear()

    def test_load_qwen_bundle_uses_http_flow_when_runtime_is_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = replace(make_settings(Path(tmp)), runtime_backend="http")
            from qwen2api.qwen_adapter import load_qwen_bundle

            bundle = load_qwen_bundle(settings)
            self.assertEqual(bundle.load_dotenv.__module__, "qwen_http_runtime.runtime")
            self.assertEqual(bundle.resolve_execution_accounts.__module__, "qwen_http_runtime.accounts")
            self.assertEqual(bundle.run_real_flow.__module__, "qwen_http_runtime.flow")
            self.assertEqual(bundle.write_result_metadata.__module__, "qwen_http_runtime.result_metadata")

    def test_build_success_payload_uses_suffix_when_flat_output_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root)
            existing_output = settings.outputs_dir / "课程？.md"
            existing_output.write_text("old\n", encoding="utf-8")

            source_dir = root / "job" / "outputs"
            source_dir.mkdir(parents=True, exist_ok=True)
            export_path = source_dir / "课程？.md"
            export_path.write_text("new\n", encoding="utf-8")
            export_path.with_suffix(".md.meta.json").write_text("{}", encoding="utf-8")

            job_payload = init_job_payload(
                "job-test",
                original_filename="课程?.mp4",
                delete_remote=True,
                account_id="",
                account_strategy="round-robin",
                queued=False,
                suggested_poll_seconds=60,
                target_markdown_name="课程？.md",
            )

            result = build_success_payload(
                settings=settings,
                job_payload=job_payload,
                flow_result=NativeFlowResult(
                    export_path=export_path,
                    record_id="record-1",
                    gen_record_id="gen-1",
                    remote_deleted=True,
                    account_id="acc-1",
                    account_label="acc-1",
                ),
            )

            self.assertEqual(result["markdown_filename"], "课程？-2.md")
            self.assertEqual(Path(result["output_file"]).name, "课程？-2.md")
            self.assertEqual(existing_output.read_text(encoding="utf-8"), "old\n")
            self.assertFalse((settings.outputs_dir / "课程？-2.md.meta.json").exists())

    def test_serialize_job_payload_drops_text_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            payload = {
                "job_id": "job-1",
                "status": "succeeded",
                "format": "md",
                "text": "# content\n",
                "meta": {},
            }
            stored = serialize_job_payload(settings, payload)
            self.assertIsNone(stored["text"])

    def test_reserve_markdown_name_prevents_cross_request_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            name1 = reserve_markdown_name(settings.runtime_dir, settings.outputs_dir, "课程?.mp4")
            name2 = reserve_markdown_name(settings.runtime_dir, settings.outputs_dir, "课程?.mp4")
            self.assertEqual(name1, "课程？.md")
            self.assertEqual(name2, "课程？-2.md")
            release_markdown_name_reservation(settings.runtime_dir, name1)
            release_markdown_name_reservation(settings.runtime_dir, name2)

    def test_recover_pending_jobs_requeues_running_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            queued_job_dir = job_dir(settings.jobs_dir, "job_queued")
            queued_input = queued_job_dir / "queued.mp4"
            queued_input.write_bytes(b"queued")
            queued_payload = init_job_payload(
                "job_queued",
                original_filename="queued.mp4",
                delete_remote=True,
                account_id="",
                account_strategy="round-robin",
                queued=True,
            )
            queued_payload["meta"]["job_dir"] = str(queued_job_dir)
            queued_payload["meta"]["input_file"] = str(queued_input)
            save_job(settings.jobs_dir, "job_queued", queued_payload)

            running_job_dir = job_dir(settings.jobs_dir, "job_running")
            running_input = running_job_dir / "running.mp4"
            running_input.write_bytes(b"running")
            running_payload = init_job_payload(
                "job_running",
                original_filename="running.mp4",
                delete_remote=True,
                account_id="",
                account_strategy="round-robin",
                queued=False,
            )
            running_payload["status"] = "running"
            running_payload["meta"]["job_dir"] = str(running_job_dir)
            running_payload["meta"]["input_file"] = str(running_input)
            save_job(settings.jobs_dir, "job_running", running_payload)

            app = FastAPI()
            app.state.settings = settings
            app.state.job_queue = asyncio.Queue()
            asyncio.run(recover_pending_jobs(app))

            self.assertEqual(app.state.job_queue.qsize(), 2)
            requeued_running = load_job(settings.jobs_dir, "job_running")
            self.assertEqual(requeued_running["status"], "queued")
            self.assertTrue(requeued_running["meta"]["requeued_after_restart"])

    def test_retry_helpers_schedule_retryable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            failed_payload = init_job_payload(
                "job_retry",
                original_filename="retry.mp4",
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

            self.assertTrue(should_retry_job(settings, failed_payload))
            retry_payload = build_retry_payload(settings, failed_payload)
            self.assertEqual(retry_payload["status"], "queued")
            self.assertEqual(retry_payload["meta"]["retry_count"], 1)
            self.assertGreaterEqual(retry_wait_seconds(retry_payload), 0)

            exhausted = {
                **retry_payload,
                "status": "failed",
                "error": {"code": "TRANSCRIPTION_TIMEOUT", "message": "timed out"},
                "meta": {
                    **retry_payload["meta"],
                    "retry_count": settings.max_retries,
                },
            }
            self.assertFalse(should_retry_job(settings, exhausted))

    def test_reporting_and_cleanup_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))

            old_job_dir = job_dir(settings.jobs_dir, "job_old")
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
            save_job(settings.jobs_dir, "job_old", old_payload)

            batch_payload = {
                "batch_id": "batch_old",
                "total": 1,
                "accepted": 1,
                "format": "md",
                "output_dir": str(settings.outputs_dir),
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
            save_batch(settings.runtime_dir, "batch_old", batch_payload)

            report = build_batch_report(settings, "batch_old")
            markdown = render_batch_report_markdown(report)
            self.assertEqual(report["counts"]["failed"], 1)
            self.assertEqual(report["rates"]["failure_rate_percent"], 100.0)
            self.assertEqual(report["error_groups"]["TRANSCRIPTION_FAILED"], 1)
            self.assertEqual(report["source_mode_groups"]["local_path"], 1)
            self.assertEqual(report["sizes"]["total_input_size_mb"], 50.0)
            self.assertIn("Failed Items", markdown)
            self.assertIn("Failure Groups", markdown)

            retries = collect_failed_retry_candidates(settings, "batch_old")
            self.assertEqual(len(retries), 1)
            self.assertEqual(retries[0].retry_path, str(old_input))

            dry_run = cleanup_jobs(settings, older_than_hours=1, statuses={"failed"}, dry_run=True)
            self.assertIn("job_old", dry_run["removed_jobs"])
            self.assertTrue((settings.jobs_dir / "job_old").exists())

            applied = cleanup_jobs(settings, older_than_hours=1, statuses={"failed"}, dry_run=False)
            self.assertIn("job_old", applied["removed_jobs"])
            self.assertFalse((settings.jobs_dir / "job_old").exists())
            self.assertEqual(batch_file(settings.runtime_dir, "batch_old").exists(), False)


if __name__ == "__main__":
    unittest.main()
