"""Build, check and publish the map styles (D1 spec §2).

CI: python -m mapstyle.main
Local: .venv/bin/python -I -B scripts/build-map-styles.py --out .build/map-styles"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from mapstyle.checks import MAX_BYTES, StyleCheckFailed, check_style
from mapstyle.publish import LATEST_KEY, build_prefix, publish_map_styles
from mapstyle.transform import encode, license_markdown, transform
from pipeline import r2
from pipeline.r2 import make_client

STYLE_NAMES = ("liberty", "fiord")
UPSTREAM = "https://tiles.openfreemap.org/styles/{name}"
HEADERS = {"User-Agent": "stormrider-data map styles (+https://github.com/brian9527/stormrider-data)"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_style(name: str, session=None, timeout: int = 60) -> tuple[dict, bytes]:
    """Reads the upstream style in chunks and stops as soon as it passes MAX_BYTES (Codex second review 9)."""
    s = session or requests.Session()
    response = s.get(UPSTREAM.format(name=name), headers=HEADERS, timeout=timeout, stream=True)
    try:
        response.raise_for_status()
        chunks, total = [], 0
        for chunk in response.iter_content(64 * 1024):
            total += len(chunk)
            if total > MAX_BYTES:
                raise StyleCheckFailed(f"{name}: upstream is over {MAX_BYTES} bytes")
            chunks.append(chunk)
    finally:
        response.close()
    raw = b"".join(chunks)
    return json.loads(raw), raw


def build(fetch=None) -> dict:
    fetch = fetch or fetch_style
    built = {}
    for name in STYLE_NAMES:
        upstream, raw = fetch(name)
        style, changed = transform(name, upstream)
        body = encode(style)
        check_style(name, style, changed, body)
        built[name] = {"body": body, "upstreamUrl": UPSTREAM.format(name=name), "upstreamSha256": sha256(raw)}
    return built


def content_hash(built: dict) -> str:
    return sha256("".join(f"{name}:{sha256(built[name]['body'])};" for name in sorted(built)).encode("utf-8"))


def build_manifest(build_id: str, built_at: str, built: dict) -> dict:
    prefix = build_prefix(build_id)
    return {
        "version": 1,
        "buildId": build_id,
        # search.publish.latest_document() needs a date; the styles have none, so the build date.
        "dataDate": built_at[:10],
        "builtAt": built_at,
        "contentSha256": content_hash(built),
        "styles": {
            name: {"key": prefix + f"{name}.json", "sha256": sha256(b["body"]), "bytes": len(b["body"]),
                   "upstream": {"url": b["upstreamUrl"], "sha256": b["upstreamSha256"]}}
            for name, b in built.items()
        },
    }


def previous_content_hash(client, bucket: str) -> str | None:
    latest = r2.get_json(client, bucket, LATEST_KEY)
    if not isinstance(latest, dict) or not isinstance(latest.get("manifest"), str):
        return None
    manifest = r2.get_json(client, bucket, latest["manifest"])
    return manifest.get("contentSha256") if isinstance(manifest, dict) else None


def write_out(folder: Path, built: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, b in built.items():
        (folder / f"{name}.json").write_bytes(b["body"])
    (folder / "LICENSE.md").write_text(license_markdown(), encoding="utf-8")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Build the StormRider map styles")
    p.add_argument("--out", help="write the styles and LICENSE.md to this folder instead of publishing")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.out and os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("publishing to R2 runs only in the map-styles workflow; use --out")
    now = datetime.now(timezone.utc)
    build_id = now.strftime("%Y%m%dT%H%M%SZ")
    built = build()
    if args.out:
        write_out(Path(args.out), built)
        print(json.dumps({n: len(b["body"]) for n, b in built.items()}))
        return 0
    client = make_client(os.environ["R2_ACCOUNT_ID"], os.environ["R2_ACCESS_KEY_ID"], os.environ["R2_SECRET_ACCESS_KEY"])
    bucket = os.environ["R2_BUCKET"]
    if previous_content_hash(client, bucket) == content_hash(built):
        print("map styles unchanged; nothing published")
        return 0
    manifest = build_manifest(build_id, now.isoformat(timespec="seconds"), built)
    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for name, b in built.items():
            path = Path(tmp) / f"{name}.json"
            path.write_bytes(b["body"])
            files[name] = path
        publish_map_styles(client, bucket, build_id, files, manifest)
    print(json.dumps({"buildId": build_id, "contentSha256": manifest["contentSha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
