"""Publish the routing graph to R2 the same way as the search data (route C spec §3.4–3.5)."""
from __future__ import annotations

from pipeline import r2
from routing.checks import CheckFailed
from search.publish import build_prefix as _build_prefix
from search.publish import publish

ROOT = "routing/"
LATEST_KEY = "routing/latest.json"
# Six months of graphs, so ROUTING_PIN_BUILD can go back that far (spec decision 13; search keeps 3).
KEEP_BUILDS = 6

LICENSE_MD = """# StormRider routing graph / StormRider 路網

This folder holds a Valhalla routing graph derived from OpenStreetMap, used by the StormRider app
to plan routes.

- Data © OpenStreetMap contributors, available under the Open Database License (ODbL) 1.0:
  https://www.openstreetmap.org/copyright and https://opendatacommons.org/licenses/odbl/1-0/
- This derived database (tiles.tar) is made available under the ODbL 1.0 as well.
- Source: the Geofabrik extract of Taiwan; its date and checksums are in manifest.json.
- How it was made: https://github.com/brian9527/stormrider-data (folder `routing/`), with the
  Valhalla image named in manifest.json (Valhalla is MIT-licensed).

本資料夾是 StormRider app 規劃路線用的 Valhalla 路網，衍生自 OpenStreetMap。

- 資料 © OpenStreetMap 貢獻者，依開放資料庫授權（ODbL）1.0 提供。
- 本衍生資料庫（tiles.tar）同樣依 ODbL 1.0 提供。
- 來源：Geofabrik 的台灣 OSM 檔；日期與檔案指紋見 manifest.json。
- 產生方式：https://github.com/brian9527/stormrider-data（`routing/` 資料夾），使用 manifest.json 記載的 Valhalla 映像（Valhalla 為 MIT 授權）。
"""


def build_prefix(build_id: str) -> str:
    return _build_prefix(build_id, ROOT)


def publish_routing(client, bucket: str, build_id: str, files: dict, manifest: dict) -> None:
    publish(client, bucket, build_id, files, manifest, root=ROOT, latest_key=LATEST_KEY, license_md=LICENSE_MD,
            keep=KEEP_BUILDS)


def previous_tiles_bytes(client, bucket: str) -> int | None:
    """The published graph's tiles.tar size, for the shrink check. None only when no routing build
    is published yet: a published one whose size cannot be read stops the publish, because taking it
    for a first build would skip that check (〔資料總審 1〕)."""
    latest = r2.get_json(client, bucket, LATEST_KEY)
    if latest is None:
        return None
    key = latest.get("manifest") if isinstance(latest, dict) else None
    manifest = r2.get_json(client, bucket, key) if isinstance(key, str) else None
    files = manifest.get("files") if isinstance(manifest, dict) else None
    tiles = files.get("tiles") if isinstance(files, dict) else None
    size = tiles.get("bytes") if isinstance(tiles, dict) else None
    # A bool is an int in Python, and zero or less would remove the lower bound.
    if type(size) is not int or size <= 0:
        raise CheckFailed("the size of the published tiles.tar cannot be read")
    return size
