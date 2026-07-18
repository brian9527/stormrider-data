from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from pipeline import decode as decode_mod
from pipeline import nomads, r2
from pipeline.cycles import FORECAST_HOURS, Cycle, candidate_cycles
from pipeline.encode import encode_precip_png, encode_wind_png
from pipeline.manifest import build_latest, build_manifest

KEEP_CYCLES = 3


def run(
    now: datetime,
    env: dict[str, str],
    *,
    client_factory=r2.make_client,
    check_complete=nomads.cycle_is_complete,
    download=nomads.download_grib,
    decode=decode_mod.decode_frame,
    log=print,
) -> str:
    bucket = env["R2_BUCKET"]
    client = client_factory(
        env["R2_ACCOUNT_ID"], env["R2_ACCESS_KEY_ID"], env["R2_SECRET_ACCESS_KEY"]
    )

    cycle = _select_cycle(now, check_complete, log)

    manifest_key = r2.cycle_key(cycle.cycle_id, "manifest.json")
    if r2.object_exists(client, bucket, manifest_key):
        _ensure_latest_current(client, bucket, cycle, log)
        return f"skipped {cycle.cycle_id} (already published)"

    for hour in FORECAST_HOURS:
        log(f"processing f{hour:03d}")
        fields = decode(download(cycle, hour))
        r2.upload_bytes(
            client, bucket, r2.cycle_key(cycle.cycle_id, f"wind_f{hour:03d}.png"),
            encode_wind_png(fields["u"], fields["v"]), r2.CACHE_IMMUTABLE,
        )
        r2.upload_bytes(
            client, bucket, r2.cycle_key(cycle.cycle_id, f"precip_f{hour:03d}.png"),
            encode_precip_png(fields["prate_mmh"]), r2.CACHE_IMMUTABLE,
        )

    r2.upload_bytes(
        client, bucket, manifest_key,
        json.dumps(build_manifest(cycle)).encode(), r2.CACHE_IMMUTABLE,
    )
    # latest.json 一定最後寫 — 中途失敗時 client 永遠看到上一個完整輪次
    r2.upload_bytes(
        client, bucket, r2.LATEST_KEY,
        json.dumps(build_latest(cycle)).encode(), r2.CACHE_LATEST,
    )

    for old in r2.cycles_to_delete(r2.list_cycle_ids(client, bucket), keep=KEEP_CYCLES):
        log(f"deleting old cycle {old}")
        r2.delete_cycle(client, bucket, old)

    return f"published {cycle.cycle_id}"


def _ensure_latest_current(client, bucket: str, cycle: Cycle, log) -> None:
    """已發布但 latest.json 缺失/過舊時修復（path 格式固定，字典序 = 時間序）。"""
    current = r2.get_json(client, bucket, r2.LATEST_KEY)
    target = build_latest(cycle)
    if current is None or current.get("path", "") < target["path"]:
        log("repairing stale latest.json")
        r2.upload_bytes(
            client, bucket, r2.LATEST_KEY,
            json.dumps(target).encode(), r2.CACHE_LATEST,
        )


def _select_cycle(now: datetime, check_complete, log) -> Cycle:
    candidates = candidate_cycles(now)
    for cycle in candidates:
        if check_complete(cycle):
            return cycle
        log(f"cycle {cycle.cycle_id} not complete on NOMADS, falling back")
    raise RuntimeError(
        f"no complete cycle among {[c.cycle_id for c in candidates]}"
    )


def main() -> None:
    result = run(datetime.now(timezone.utc), dict(os.environ))
    print(result)


if __name__ == "__main__":
    sys.exit(main())
