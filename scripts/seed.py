#!/usr/bin/env python3
"""Upload sample photos to the local Immich sandbox.

Endpoint verified against the Immich v3.3.1 OpenAPI spec:
  POST /api/assets (multipart: assetData, fileCreatedAt, fileModifiedAt)
  -> 201 {id, status: created} or 200 {id, status: duplicate}

Re-running is safe: Immich de-duplicates by file hash, so already-uploaded
photos are reported as duplicates and skipped.

Usage: python scripts/seed.py [--source data/sample_images] [--count N] [--workers 4]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
TIMEOUT = 60.0


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def read_env_value(env_file: Path, name: str) -> str:
    if not env_file.exists():
        return ""
    for line in env_file.read_text().splitlines():
        m = re.match(rf"^{name}=(.*)$", line)
        if m:
            return m.group(1).strip().strip("\"'")
    return ""


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def upload(client: httpx.Client, path: Path) -> str:
    """Upload one file. Returns 'created', 'duplicate' or 'failed:<reason>'."""
    st = path.stat()
    try:
        with path.open("rb") as f:
            r = client.post(
                "/api/assets",
                data={
                    "fileCreatedAt": iso(st.st_mtime),
                    "fileModifiedAt": iso(st.st_mtime),
                    "filename": path.name,
                },
                files={"assetData": (path.name, f)},
            )
    except httpx.HTTPError as e:
        return f"failed:{type(e).__name__}"
    if r.status_code in (200, 201):
        return str(r.json().get("status", "created")).lower()
    return f"failed:HTTP {r.status_code}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", type=Path, default=ROOT / "data" / "sample_images")
    ap.add_argument("--count", type=int, default=0, help="max photos to upload (0 = all)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--url", default=os.environ.get("IMMICH_URL", "http://127.0.0.1:2283"))
    ap.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = ap.parse_args()

    host = httpx.URL(args.url).host
    if host not in ("127.0.0.1", "localhost", "::1"):
        die(f"refusing non-local target {host!r}; ShutterStorm only targets the local sandbox")

    key = os.environ.get("IMMICH_API_KEY") or read_env_value(args.env_file, "IMMICH_API_KEY")
    if not key:
        die("no IMMICH_API_KEY found. Run: make bootstrap")

    files = sorted(
        p for p in args.source.glob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    if not files:
        die(f"no images found in {args.source}")
    if args.count > 0:
        files = files[: args.count]

    with httpx.Client(base_url=args.url, headers={"x-api-key": key}, timeout=TIMEOUT) as client:
        if client.get("/api/users/me").status_code != 200:
            die("API key rejected. Run: make bootstrap")

        print(f"Uploading {len(files)} photos from {args.source} ...")
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(lambda p: upload(client, p), files))

        stats = client.get("/api/assets/statistics").json()

    summary = Counter(r.split(":")[0] for r in results)
    for status, n in sorted(summary.items()):
        print(f"  {status}: {n}")
    for path, res in zip(files, results, strict=True):
        if res.startswith("failed"):
            print(f"  FAILED {path.name}: {res}", file=sys.stderr)
    print(f"Library now holds {stats.get('images', '?')} images, {stats.get('videos', '?')} videos.")
    if summary.get("failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
