from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qwen2api.config import Settings, get_settings
from qwen2api.job_queue import recover_pending_jobs
from qwen2api.main import create_app
from qwen2api.qwen_adapter import NativeFlowResult
from qwen2api.service import build_success_payload, serialize_job_payload
from qwen2api.storage import (
    init_job_payload,
    job_dir,
    load_job,
    release_markdown_name_reservation,
    reserve_markdown_name,
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
        self.assertEqual(body["markdown_filename"], "课程?.md")
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
        self.assertEqual(body["markdown_filename"], "课程?.md")
        self.assertEqual(body["suggested_poll_after_seconds"], 60)

    def test_local_batch_submission_uses_source_paths_and_returns_unique_names(self) -> None:
        left_dir = self.root / "left"
        right_dir = self.root / "right"
        left_dir.mkdir()
        right_dir.mkdir()
        left_file = left_dir / "课程?.mp4"
        right_file = right_dir / "课程?.mp4"
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
        self.assertEqual(body["items"][0]["markdown_filename"], "课程?.md")
        self.assertEqual(body["items"][1]["markdown_filename"], "课程?-2.md")

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


class ServiceTests(unittest.TestCase):
    def test_build_success_payload_uses_suffix_when_flat_output_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root)
            existing_output = settings.outputs_dir / "课程?.md"
            existing_output.write_text("old\n", encoding="utf-8")

            source_dir = root / "job" / "outputs"
            source_dir.mkdir(parents=True, exist_ok=True)
            export_path = source_dir / "课程?.md"
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
                target_markdown_name="课程?.md",
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

            self.assertEqual(result["markdown_filename"], "课程?-2.md")
            self.assertEqual(Path(result["output_file"]).name, "课程?-2.md")
            self.assertEqual(existing_output.read_text(encoding="utf-8"), "old\n")
            self.assertFalse((settings.outputs_dir / "课程?-2.md.meta.json").exists())

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
            self.assertEqual(name1, "课程?.md")
            self.assertEqual(name2, "课程?-2.md")
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


if __name__ == "__main__":
    unittest.main()
