#!/usr/bin/env python3
"""Generate a minimal auth file from a legacy Playwright storage state.

This script extracts the essential `tongyi_sso_ticket` cookie from a full
Playwright storage-state JSON and writes a clean minimal auth file.

Usage:
    # Convert existing legacy auth file
    PYTHONPATH=src python scripts/convert_to_minimal_auth.py \\
        --input .auth/qwen-storage-state.json \\
        --out .auth/minimal.json

    # Or directly specify the ticket value
    PYTHONPATH=src python scripts/convert_to_minimal_auth.py \\
        --ticket "your-cookie-value" \\
        --out .auth/minimal.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add src to path for direct execution
src_dir = Path(__file__).resolve().parents[1] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from qwen_http_runtime.minimal_auth import (
    load_auth_file,
    is_legacy_storage_format,
    is_minimal_auth_format,
    convert_legacy_to_minimal,
    create_minimal_auth,
    write_minimal_auth,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert legacy Playwright auth file to minimal format, or create minimal auth from a ticket value."
    )
    parser.add_argument(
        "--input",
        help="Path to legacy Playwright storage-state JSON file",
    )
    parser.add_argument(
        "--ticket",
        help="Direct tongyi_sso_ticket value (alternative to --input)",
    )
    parser.add_argument(
        "--out",
        default=".auth/minimal.json",
        help="Output minimal auth file path. Default: .auth/minimal.json",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite output file if it exists",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.out).expanduser().resolve()

    # Validate output path
    if output_path.exists() and not args.force:
        print(f"[error] Output file already exists: {output_path}", file=sys.stderr)
        print(f"[hint] Use --force to overwrite", file=sys.stderr)
        return 1

    # Validate input
    if not args.input and not args.ticket:
        print("[error] Must specify either --input or --ticket", file=sys.stderr)
        return 1

    if args.input and args.ticket:
        print("[error] Cannot specify both --input and --ticket", file=sys.stderr)
        return 1

    try:
        if args.ticket:
            # Direct ticket value
            print(f"[convert] Creating minimal auth from direct ticket value")
            minimal = create_minimal_auth(args.ticket)
        else:
            # Convert from legacy file
            input_path = Path(args.input).expanduser().resolve()
            print(f"[convert] Loading legacy auth file: {input_path}")
            
            payload = load_auth_file(input_path)
            
            if is_minimal_auth_format(payload):
                print(f"[convert] Input is already in minimal format")
                minimal = {"tongyi_sso_ticket": payload["tongyi_sso_ticket"]}
            elif is_legacy_storage_format(payload):
                print(f"[convert] Detected legacy storage format, converting...")
                minimal = convert_legacy_to_minimal(payload)
                if minimal is None:
                    print(f"[error] Could not find tongyi_sso_ticket cookie in legacy file", file=sys.stderr)
                    return 1
            else:
                print(f"[error] Unsupported auth format in {input_path}", file=sys.stderr)
                return 1

        # Write output
        write_minimal_auth(output_path, minimal["tongyi_sso_ticket"])
        print(f"[success] Wrote minimal auth file: {output_path}")
        print(f"[info] You can now use this file with:")
        print(f"       QWEN2API_QWEN_AUTH_STATE={output_path}")
        return 0

    except FileNotFoundError as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"[error] Unexpected error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
