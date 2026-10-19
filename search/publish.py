"""Publish the monthly search data to R2 and keep the newest three builds (backend B spec §3.6).

Each run gets its own build id (UTC time to the second) and folder, written once and never
overwritten; latest.json is written last. The server compares build ids, not dates."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import r2

LATEST_KEY = "search/latest.json"
KEEP_BUILDS = 3
CACHE_BUILD = "public, max-age=86400, immutable"
CACHE_LATEST = "public, max-age=300"
CONTENT_TYPES = {".gz": "application/gzip", ".json": "application/json", ".md": "text/markdown; charset=utf-8"}

LICENSE_MD = """# StormRider place data / StormRider 地點資料

This folder holds a database derived from OpenStreetMap, used by the StormRider app's place search.

- Data © OpenStreetMap contributors, available under the Open Database License (ODbL) 1.0:
  https://www.openstreetmap.org/copyright and https://opendatacommons.org/licenses/odbl/1-0/
- This derived database is made available under the ODbL 1.0 as well.
- Sources: the Photon data dump for Taiwan (GraphHopper, built from OpenStreetMap) and the Geofabrik
  extract of Taiwan. Their dates and checksums are in manifest.json.
- How it was made: https://github.com/brian9527/stormrider-data (folder `search/`).

本資料夾是 StormRider app 地點搜尋用的資料庫，衍生自 OpenStreetMap。

- 資料 © OpenStreetMap 貢獻者，依開放資料庫授權（ODbL）1.0 提供。
- 本衍生資料庫同樣依 ODbL 1.0 提供。
- 來源：GraphHopper 的台灣 Photon 資料包（由 OpenStreetMap 產生）、Geofabrik 的台灣 OSM 檔；日期與檔案指紋見 manifest.json。
- 產生方式：https://github.com/brian9527/stormrider-data（`search/` 資料夾）。
"""


class AlreadyPublished(Exception):
    pass


def build_prefix(build_id: str) -> str:
    return f"search/{build_id}/"


def _content_type(key: str) -> str:
    return CONTENT_TYPES.get(Path(key).suffix, "application/octet-stream")


def _put(client, bucket: str, key: str, body: bytes, cache: str) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=_content_type(key), CacheControl=cache)


def latest_document(build_id: str, data_date: str) -> dict:
    return {"buildId": build_id, "dataDate": data_date, "manifest": build_prefix(build_id) + "manifest.json"}


def _folder_has_objects(client, bucket: str, prefix: str) -> bool:
    return bool(client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1).get("Contents"))


def publish(client, bucket: str, build_id: str, files: dict[str, Path], manifest: dict) -> None:
    prefix = build_prefix(build_id)
    # Anything in the folder — even a partial upload without a manifest — means this build was
    # published before. Only the workflow publishes to R2, and its concurrency group runs one
    # publisher at a time (plan Task 4), so check-then-write cannot race another publisher.
    if _folder_has_objects(client, bucket, prefix):
        raise AlreadyPublished(f"{prefix} already exists")
    for path in files.values():
        _put(client, bucket, prefix + path.name, path.read_bytes(), CACHE_BUILD)
    _put(client, bucket, prefix + "LICENSE.md", LICENSE_MD.encode("utf-8"), CACHE_BUILD)
    _put(client, bucket, prefix + "manifest.json",
         json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"), CACHE_BUILD)
    # The build latest.json named until now: readers may hold that pointer for CACHE_LATEST's
    # five minutes after it changes, so this publish must not delete it.
    replaced = _latest_build_id(r2.get_json(client, bucket, LATEST_KEY))
    latest = latest_document(build_id, manifest["dataDate"])
    _put(client, bucket, LATEST_KEY, json.dumps(latest).encode("utf-8"), CACHE_LATEST)
    prune(client, bucket, also_keep=[replaced] if replaced else [])


def previous_counts(client, bucket: str) -> dict | None:
    latest = r2.get_json(client, bucket, LATEST_KEY)
    if not latest:
        return None
    manifest = r2.get_json(client, bucket, latest["manifest"])
    if not manifest:
        return None
    return {"places": manifest["files"]["places"]["documents"], "nearby": manifest["files"]["nearby"]["documents"]}


def _latest_build_id(doc) -> str | None:
    build_id = doc.get("buildId") if isinstance(doc, dict) else None
    return build_id if isinstance(build_id, str) and build_id else None


def prune(client, bucket: str, keep: int = KEEP_BUILDS, also_keep=()) -> None:
    """Keep the newest `keep` complete builds (those with a manifest), the one latest.json names and
    the builds in `also_keep`; delete every other folder, partial ones from failed runs included, so
    they never take a slot. Safe only with the single publisher this workflow guarantees (main.py
    refuses to publish elsewhere). Keeping just the pointer publish() replaced is enough for the
    five-minute cache: one run (download, 20 GB conversion, probes) takes far longer than that, so
    any older pointer expired before this publish began."""
    resp = client.list_objects_v2(Bucket=bucket, Prefix="search/", Delimiter="/")
    builds = sorted(p["Prefix"] for p in resp.get("CommonPrefixes", []))
    complete = [p for p in builds if r2.object_exists(client, bucket, p + "manifest.json")]
    kept = set(complete[-keep:]) | {build_prefix(b) for b in also_keep}
    current = _latest_build_id(r2.get_json(client, bucket, LATEST_KEY))
    if current:
        kept.add(build_prefix(current))
    for prefix in builds:
        if prefix in kept:
            continue
        listed = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        keys = [{"Key": o["Key"]} for o in listed.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
