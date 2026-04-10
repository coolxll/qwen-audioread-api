from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from qwen2api.config import get_settings
from qwen2api.maintenance import cleanup_jobs, collect_failed_retry_candidates
from qwen2api.reporting import build_batch_report, render_batch_report_markdown, report_output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch report / cleanup / retry tools for qwen2api.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report = subparsers.add_parser("report", help="Export a batch report.")
    report.add_argument("--batch-id", required=True)
    report.add_argument("--format", choices=["md", "json"], default="md")
    report.add_argument("--output", help="Optional output file path.")

    cleanup = subparsers.add_parser("cleanup", help="Remove old job history.")
    cleanup.add_argument("--older-than-hours", type=float, default=24.0)
    cleanup.add_argument("--statuses", default="succeeded,failed")
    cleanup.add_argument("--apply", action="store_true", help="Actually delete; default is dry-run.")

    retry = subparsers.add_parser("retry-failed", help="Retry failed jobs from a batch via local async endpoint.")
    retry.add_argument("--batch-id", required=True)
    retry.add_argument("--endpoint", default="http://127.0.0.1:18000")
    return parser.parse_args()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def request_json(method: str, url: str, payload: dict | None = None) -> dict:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise SystemExit(f"Request failed: {error}") from error


def run_report(args: argparse.Namespace) -> int:
    settings = get_settings()
    report = build_batch_report(settings, args.batch_id)
    if args.format == "json":
        output = json.dumps(report, ensure_ascii=False, indent=2)
        output_path = Path(args.output) if args.output else report_output_path(settings, args.batch_id, "json")
    else:
        output = render_batch_report_markdown(report)
        output_path = Path(args.output) if args.output else report_output_path(settings, args.batch_id, "md")

    write_text(output_path, output)
    print(output_path)
    return 0


def run_cleanup(args: argparse.Namespace) -> int:
    settings = get_settings()
    statuses = {status.strip() for status in args.statuses.split(",") if status.strip()}
    result = cleanup_jobs(
        settings,
        older_than_hours=args.older_than_hours,
        statuses=statuses,
        dry_run=not args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def run_retry_failed(args: argparse.Namespace) -> int:
    settings = get_settings()
    candidates = collect_failed_retry_candidates(settings, args.batch_id)
    results: list[dict] = []
    for candidate in candidates:
        payload = request_json(
            "POST",
            f"{args.endpoint.rstrip('/')}/api/v1/transcriptions/local/async",
            {
                "path": candidate.retry_path,
                "format": "md",
                "delete_remote": candidate.delete_remote,
                "account": candidate.account,
                "account_strategy": candidate.account_strategy,
            },
        )
        results.append(
            {
                "from_job_id": candidate.job_id,
                "original_filename": candidate.original_filename,
                "retry_path": candidate.retry_path,
                "new_job_id": payload["job_id"],
                "markdown_filename": payload["markdown_filename"],
                "status": payload["status"],
            }
        )
    print(json.dumps({"batch_id": args.batch_id, "retried": results}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "report":
        return run_report(args)
    if args.command == "cleanup":
        return run_cleanup(args)
    if args.command == "retry-failed":
        return run_retry_failed(args)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
