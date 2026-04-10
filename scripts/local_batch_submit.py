from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit local files to qwen2api local batch endpoint.")
    parser.add_argument("paths", nargs="*", help="Local file paths.")
    parser.add_argument("--paths-file", help="Text file with one local file path per line.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:18000", help="qwen2api base URL.")
    parser.add_argument("--format", default="md", help="Export format, md only.")
    parser.add_argument("--poll", action="store_true", help="Poll batch status until all jobs finish.")
    parser.add_argument("--interval", type=float, default=30.0, help="Polling interval seconds.")
    parser.add_argument("--timeout", type=float, default=3600.0, help="Polling timeout seconds.")
    parser.add_argument("--account", default="", help="Optional account id.")
    parser.add_argument("--account-strategy", default="round-robin", help="Account strategy.")
    parser.add_argument(
        "--delete-remote",
        choices=["true", "false"],
        default=None,
        help="Override delete_remote.",
    )
    return parser.parse_args()


def load_paths(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    if args.paths_file:
        values.extend(
            line.strip()
            for line in Path(args.paths_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    values.extend(args.paths)
    if not values:
        raise SystemExit("No input paths provided.")
    return [str(Path(path).expanduser().resolve()) for path in values]


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


def submit_batch(args: argparse.Namespace, paths: list[str]) -> dict:
    payload: dict = {
        "paths": paths,
        "format": args.format,
        "account": args.account,
        "account_strategy": args.account_strategy,
    }
    if args.delete_remote is not None:
        payload["delete_remote"] = args.delete_remote == "true"
    return request_json("POST", f"{args.endpoint.rstrip('/')}/api/v1/transcriptions/local/batch", payload)


def summarize_batch(batch: dict) -> dict:
    counts: dict[str, int] = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0}
    for item in batch.get("items", []):
        status = item.get("status", "queued")
        counts[status] = counts.get(status, 0) + 1
    return counts


def poll_batch(args: argparse.Namespace, batch_id: str) -> dict:
    deadline = time.time() + args.timeout
    url = f"{args.endpoint.rstrip('/')}/api/v1/batches/{batch_id}"
    while True:
        batch = request_json("GET", url)
        counts = summarize_batch(batch)
        print(json.dumps({"batch_id": batch_id, "counts": counts}, ensure_ascii=False))
        if counts["queued"] == 0 and counts["running"] == 0:
            return batch
        if time.time() >= deadline:
            raise SystemExit(f"Polling timed out for batch {batch_id}.")
        time.sleep(args.interval)


def main() -> int:
    args = parse_args()
    paths = load_paths(args)
    batch = submit_batch(args, paths)
    print(
        json.dumps(
            {
                "batch_id": batch["batch_id"],
                "total": batch["total"],
                "accepted": batch["accepted"],
                "output_dir": batch["output_dir"],
                "items": batch["items"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.poll:
        final_batch = poll_batch(args, batch["batch_id"])
        print(json.dumps({"final_batch_id": final_batch["batch_id"], "items": final_batch["items"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
