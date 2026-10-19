"""Build, check and publish the monthly search data (backend B spec §3).

CI: python -m search.main --workdir "$RUNNER_TEMP/search"
Local: .venv/bin/python -I -B scripts/build-search.py --workdir .build/search --skip-publish"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pipeline.r2 import make_client
from search.checks import (PROBES, CheckFailed, check_counts, check_source_fresh, require_probes, run_probes,
                           source_date)
from search.meili import PLACES_SETTINGS, Meili
from search.nearby import extract_nearby, pbf_timestamp
from search.places import PlaceStats, build_documents, iter_dump_records, sha256_file, write_ndjson_gz
from search.publish import LATEST_KEY, LICENSE_MD, build_prefix, latest_document, previous_counts, publish
from search.sources import DOWNLOAD_ATTEMPTS, GEOFABRIK_URL, PHOTON_URL, download

CHECK_INDEX = "check_places"


# The data time in the dump header: Photon JSON dump format 0.1.0 §3 (app repo
# research/2026-10-09-search-improvement/evidence/photon-json-dump-format-0.1.0.md). The Taiwan dump
# of 2026-10-03 had it (results/dump-snapshot.txt there). No other field is a stand-in for it.
PHOTON_TIME_KEY = "content.data_timestamp"


def photon_timestamp(header: dict) -> str | None:
    value = header.get(PHOTON_TIME_KEY)
    return value if isinstance(value, str) and source_date(value) else None


def build_outputs(photon_path: Path, pbf_path: Path, workdir: Path, *, merge_roads: bool = True,
                  exclude: bool = True) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    pstats, header = PlaceStats(), {}
    places_path = workdir / "places.ndjson.gz"
    with open(photon_path, "rb") as f:
        docs = build_documents(iter_dump_records(f, pstats, header), pstats, merge_roads=merge_roads, exclude=exclude)
        n_places, sha_places = write_ndjson_gz(docs, places_path)
    items, nstats = extract_nearby(pbf_path)
    nearby_path = workdir / "nearby.ndjson.gz"
    n_nearby, sha_nearby = write_ndjson_gz(items, nearby_path)
    probes_path = workdir / "probes.json"
    probes_path.write_text(json.dumps(PROBES, ensure_ascii=False, indent=1), encoding="utf-8")
    photon_ts, pbf_ts = photon_timestamp(header), pbf_timestamp(pbf_path)
    return {
        "places": {"path": places_path, "sha256": sha_places, "documents": n_places},
        "nearby": {"path": nearby_path, "sha256": sha_nearby, "documents": n_nearby},
        "probes": {"path": probes_path, "sha256": sha256_file(probes_path)},
        "header": header,
        "timestamps": {"photon": photon_ts, "geofabrik": pbf_ts},
        "dataDate": source_date(photon_ts, pbf_ts),
        "stats": {"places": pstats.as_dict(), "nearby": nstats.as_dict()},
    }


def build_manifest(build_id: str, built_at: str, sources: dict, out: dict) -> dict:
    prefix = build_prefix(build_id)
    files = {}
    for name in ("places", "nearby", "probes"):
        entry = {"key": prefix + out[name]["path"].name, "sha256": out[name]["sha256"]}
        if "documents" in out[name]:
            entry["documents"] = out[name]["documents"]
        files[name] = entry
    return {
        "version": 1,
        "buildId": build_id,
        # The older of the two inputs' data times — what /health measures freshness by (spec §4.3).
        "dataDate": out["dataDate"],
        "builtAt": built_at,
        "sources": {
            "photon": {**sources["photon"], "timestamp": out["timestamps"]["photon"], "header": out["header"]},
            "geofabrik": {**sources["geofabrik"], "timestamp": out["timestamps"]["geofabrik"]},
        },
        "files": files,
        "stats": out["stats"],
    }


def local_publish(site: Path, build_id: str, built_at: str, out: dict, sources: dict) -> dict:
    """Same layout as R2 under a local folder, for the dev-set comparison (plan Task 11). The server
    imports it the same way, so it needs a data date like a real publish (freshness is not checked)."""
    if out["dataDate"] is None:
        raise CheckFailed("the data time of the inputs is unknown; the server would refuse this build")
    manifest = build_manifest(build_id, built_at, sources, out)
    folder = site / build_prefix(build_id)
    folder.mkdir(parents=True, exist_ok=False)
    for name in ("places", "nearby", "probes"):
        shutil.copyfile(out[name]["path"], folder / out[name]["path"].name)
    (folder / "LICENSE.md").write_text(LICENSE_MD, encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    latest = latest_document(build_id, manifest["dataDate"])
    (site / LATEST_KEY).write_text(json.dumps(latest), encoding="utf-8")
    return manifest


def _probe_check(places_path: Path) -> None:
    m = Meili(os.environ.get("MEILI_URL", "http://127.0.0.1:7700"), os.environ["MEILI_MASTER_KEY"])
    m.recreate_index(CHECK_INDEX, "doc_id")
    m.update_settings(CHECK_INDEX, PLACES_SETTINGS)
    m.add_ndjson_gz(CHECK_INDEX, places_path)
    passed, total, failed = run_probes(lambda q: m.search(CHECK_INDEX, q))
    print(json.dumps({"probes_passed": passed, "probes_total": total, "failed": failed}, ensure_ascii=False))
    require_probes(passed, total)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Build the monthly search data")
    p.add_argument("--workdir", required=True)
    p.add_argument("--photon-file", help="use a local Photon dump instead of downloading")
    p.add_argument("--pbf-file", help="use a local Geofabrik extract instead of downloading")
    p.add_argument("--no-road-merge", action="store_true", help="evaluation only (plan Task 11)")
    p.add_argument("--keep-excluded", action="store_true", help="evaluation only (plan Task 11)")
    p.add_argument("--skip-probes", action="store_true", help="local runs without Meilisearch")
    p.add_argument("--skip-publish", action="store_true")
    p.add_argument("--local-publish", help="write the R2 layout to this folder instead (plan Task 11)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    publishing = not args.skip_publish and not args.local_publish
    # Only the search-data workflow publishes to R2, one run at a time (its concurrency group);
    # publish() and prune() rely on that single writer, so a local run with R2 keys must not.
    if publishing and os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("publishing to R2 runs only in the search-data workflow; "
                         "use --skip-publish or --local-publish")
    now = datetime.now(timezone.utc)
    build_id = now.strftime("%Y%m%dT%H%M%SZ")
    built_at = now.isoformat(timespec="seconds")

    sources = {}
    if args.photon_file:
        photon = Path(args.photon_file)
        sources["photon"] = {"url": None, "sha256": sha256_file(photon)}
    else:
        photon = workdir / "photon.jsonl.zst"
        sources["photon"] = download(PHOTON_URL, photon, attempts=DOWNLOAD_ATTEMPTS)
    if args.pbf_file:
        pbf = Path(args.pbf_file)
        sources["geofabrik"] = {"url": None, "sha256": sha256_file(pbf)}
    else:
        pbf = workdir / "taiwan-latest.osm.pbf"
        sources["geofabrik"] = download(GEOFABRIK_URL, pbf, attempts=DOWNLOAD_ATTEMPTS)

    out = build_outputs(photon, pbf, workdir / f"out-{build_id}", merge_roads=not args.no_road_merge,
                        exclude=not args.keep_excluded)
    print(json.dumps({"buildId": build_id, "dataDate": out["dataDate"], "timestamps": out["timestamps"],
                      "counts": {"places": out["places"]["documents"], "nearby": out["nearby"]["documents"]},
                      "stats": out["stats"]}, ensure_ascii=False))
    if out["stats"]["places"]["decode_errors"]:
        # Bad JSON, a missing header or a malformed envelope (search/places.py PlaceStats).
        raise CheckFailed(f"the Photon dump breaks its format in {out['stats']['places']['decode_errors']} places")

    client = bucket = None
    previous = None
    if publishing:
        check_source_fresh(out["dataDate"], built_at)
        client = make_client(os.environ["R2_ACCOUNT_ID"], os.environ["R2_ACCESS_KEY_ID"],
                             os.environ["R2_SECRET_ACCESS_KEY"])
        bucket = os.environ["R2_BUCKET"]
        previous = previous_counts(client, bucket)
    elif out["dataDate"] is None:
        print("warning: the data time of the inputs is unknown (only checked when publishing)")
    check_counts(out["places"]["documents"], out["nearby"]["documents"], previous)
    if not args.skip_probes:
        _probe_check(out["places"]["path"])

    if args.local_publish:
        local_publish(Path(args.local_publish), build_id, built_at, out, sources)
    elif publishing:
        publish(client, bucket, build_id,
                {name: out[name]["path"] for name in ("places", "nearby", "probes")},
                build_manifest(build_id, built_at, sources, out))
    print(json.dumps({"published": publishing or bool(args.local_publish), "buildId": build_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
