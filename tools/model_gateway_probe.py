#!/usr/bin/env python3
"""No-network gateway preflight. No paid command or inference entry point.

Use --local-native ONLY to run OpenCode --version and reviewed auth list.
Metadata/reviews are explicit sanitized operator files, never credential files.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nova3_team.model_gateway import (EndpointEvidence, GatewayConfig, GatewayError,
    Review, native_status, opencode_settings, preflight, write_private_report,
    write_user_settings)


def read_metadata(path: Path) -> bytes:
    """Bounded regular files only. Never print their bytes or exception text."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if path.is_symlink():
        raise GatewayError("symlink_denied")
    fd = os.open(path, flags)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        os.close(fd)
        raise GatewayError("metadata_size_or_type_denied")
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(65537)
        if len(raw) > 65536:
            raise GatewayError("metadata_size_or_type_denied")
        return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--endpoint-evidence", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--opencode-schema", type=Path)
    parser.add_argument("--offline", action="store_true", help="Ignore endpoint/review files; never invoke OpenCode")
    parser.add_argument("--local-native", action="store_true", help="Explicit opt-in to local CLI status, never completions")
    parser.add_argument("--cache", type=Path, default=Path(".cache"))
    parser.add_argument("--stdout-only", action="store_true", help="Do not write a cache report")
    parser.add_argument("--write-user-config", type=Path, help="Exclusive creation of ~/.config/opencode/opencode.json after PASS")
    args = parser.parse_args(argv)
    try:
        if args.offline and args.local_native:
            raise GatewayError("offline_native_conflict")
        if (args.review is None) != (args.opencode_schema is None):
            raise GatewayError("review_and_schema_required_together")
        config = GatewayConfig.parse(json.loads(read_metadata(args.config)))
        endpoint = review = None
        if not args.offline:
            if args.endpoint_evidence:
                endpoint = EndpointEvidence.parse(json.loads(read_metadata(args.endpoint_evidence)))
            if args.review:
                review = Review.parse(json.loads(read_metadata(args.review)), read_metadata(args.opencode_schema))
        native = native_status(review, enabled=args.local_native)
        now = int(time.time())
        report = preflight(config, endpoint, review, native, now=now)
        if args.write_user_config:
            if endpoint is None or review is None:
                raise GatewayError("readiness_not_passed")
            settings = opencode_settings(config, endpoint, review, native, now=now)
            write_user_settings(args.write_user_config, settings)
        if not args.stdout_only:
            write_private_report(args.cache, report)
        print(json.dumps(report, sort_keys=True, allow_nan=False))
        return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}[report["status"]]
    except (OSError, ValueError, TypeError, RecursionError):
        # Even malformed inputs/errors may contain secrets. Emit no raw details.
        print(json.dumps({"schema": "nova3.gateway-status/1", "status": "FAIL",
                          "error": "invalid_input_or_local_operation",
                          "paid_requests": 0}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
