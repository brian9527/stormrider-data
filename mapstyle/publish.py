"""Publish the map styles to R2 the same way as the search data and the routing graph (D1 spec §2.1)."""
from __future__ import annotations

from mapstyle.transform import license_markdown
from search.publish import build_prefix as _build_prefix
from search.publish import publish

ROOT = "map/"
LATEST_KEY = "map/latest.json"
KEEP_BUILDS = 6


def build_prefix(build_id: str) -> str:
    return _build_prefix(build_id, ROOT)


def publish_map_styles(client, bucket: str, build_id: str, files: dict, manifest: dict) -> None:
    publish(client, bucket, build_id, files, manifest, root=ROOT, latest_key=LATEST_KEY,
            license_md=license_markdown(), keep=KEEP_BUILDS)
