"""Publish the map styles to R2 the same way as the search data and the routing graph (D1 spec §2.1)."""
from __future__ import annotations

from mapstyle.transform import license_markdown
from search.publish import build_prefix as _build_prefix
from search.publish import publish

ROOT = "map/"
LATEST_KEY = "map/latest.json"
# search/publish.py's prune() also keeps the build latest.json named before this publish, and says that
# is enough for the five-minute cache of latest.json because a search run takes far longer. A map run
# takes seconds, so that reason does not carry over. What protects a cached pointer here is KEEP_BUILDS:
# a build latest.json named is deleted only once six newer complete builds exist, and a map build is
# made only when the styles changed (monthly), so that needs six changed publishes inside five minutes.
# Even then a phone's refresh just fails and it keeps the style it has (Codex task 1-3 review 1).
KEEP_BUILDS = 6


def build_prefix(build_id: str) -> str:
    return _build_prefix(build_id, ROOT)


def publish_map_styles(client, bucket: str, build_id: str, files: dict, manifest: dict) -> None:
    publish(client, bucket, build_id, files, manifest, root=ROOT, latest_key=LATEST_KEY,
            license_md=license_markdown(), keep=KEEP_BUILDS)
