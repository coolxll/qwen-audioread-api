from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from qwen2api.config import Settings, get_settings
from qwen2api.main import create_app
from qwen2api.qwen_adapter import NativeFlowResult
from qwen2api.service import build_success_payload
from qwen2api.storage import init_job_payload


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
    )
    settings.ensure_directories()
    return settings


class TranscriptionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.settings = make_settings(self.root)
        app = create_app()
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
                files={"file": ("demo.mp4", b"video-bytes", "video/mp4")},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["format"], "md")
        self.assertEqual(body["markdown_filename"], "demo.md")
        self.assertEqual(body["suggested_poll_after_seconds"], 60)

    def test_async_transcription_returns_queue_metadata(self) -> None:
        with patch("qwen2api.api.transcriptions.run_transcription_background", new=AsyncMock(return_value=None)):
            response = self.client.post(
                "/api/v1/transcriptions/async",
                data={"format": "md"},
                files={"file": ("demo.mp4", b"video-bytes", "video/mp4")},
            )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["markdown_filename"], "demo.md")
        self.assertEqual(body["suggested_poll_after_seconds"], 60)

    def test_batch_submission_and_batch_lookup(self) -> None:
        with patch("qwen2api.api.transcriptions.run_transcription_background", new=AsyncMock(return_value=None)):
            response = self.client.post(
                "/api/v1/transcriptions/batch",
                data={"format": "md"},
                files=[
                    ("files", ("课程.mp4", b"a", "video/mp4")),
                    ("files", ("课程.mp4", b"b", "video/mp4")),
                ],
            )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertIn("batch_id", body)
        self.assertEqual(body["output_dir"], str(self.settings.outputs_dir))
        self.assertEqual(body["items"][0]["markdown_filename"], "课程.md")
        self.assertEqual(body["items"][1]["markdown_filename"], "课程-2.md")
        self.assertEqual(body["items"][0]["suggested_poll_after_seconds"], 60)

        batch_response = self.client.get(f"/api/v1/batches/{body['batch_id']}")
        self.assertEqual(batch_response.status_code, 200)
        batch_body = batch_response.json()
        self.assertEqual(batch_body["batch_id"], body["batch_id"])
        self.assertEqual(len(batch_body["items"]), 2)

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
            existing_output = settings.outputs_dir / "课程.md"
            existing_output.write_text("old\n", encoding="utf-8")

            source_dir = root / "job" / "outputs"
            source_dir.mkdir(parents=True, exist_ok=True)
            export_path = source_dir / "课程.md"
            export_path.write_text("new\n", encoding="utf-8")

            job_payload = init_job_payload(
                "job-test",
                original_filename="课程.mp4",
                delete_remote=True,
                account_id="",
                account_strategy="round-robin",
                queued=False,
                suggested_poll_seconds=60,
                target_markdown_name="课程.md",
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

            self.assertEqual(result["markdown_filename"], "课程-2.md")
            self.assertEqual(Path(result["output_file"]).name, "课程-2.md")
            self.assertEqual(existing_output.read_text(encoding="utf-8"), "old\n")


if __name__ == "__main__":
    unittest.main()
