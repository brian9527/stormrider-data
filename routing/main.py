"""Build, check and publish the monthly routing graph (route C spec §3).

CI: python -m routing.main --workdir "$RUNNER_TEMP/routing" --pbf-file ... --pbf-record ...
Local: .venv/bin/python -I -B scripts/build-routing.py --workdir .build/routing --pbf-file ... --local-publish .build/site"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pipeline.r2 import make_client
from routing.build import CONFIG_NAME, VALHALLA_IMAGE, build_graph, serving
from routing.checks import CheckFailed, check_tiles_size, checks_document, run_checks
from routing.publish import LATEST_KEY, LICENSE_MD, build_prefix, previous_tiles_bytes, publish_routing
from routing.valhalla import ValhallaClient
from search.checks import check_source_fresh, source_date
from search.nearby import pbf_timestamp
from search.places import sha256_file
from search.publish import latest_document
from search.sources import load_pbf_record


def build_manifest(build_id: str, built_at: str, source: dict, data_date: str | None, tiles: Path, checks: Path,
                   build_config: Path, report: list) -> dict:
    prefix = build_prefix(build_id)
    return {
        "version": 1,
        "buildId": build_id,
        "dataDate": data_date,
        "builtAt": built_at,
        "valhallaImage": VALHALLA_IMAGE,
        # Build settings and image digest together are how the graph was made (ODbL §4.6, spec §3.5).
        "buildConfigSha256": sha256_file(build_config),
        "sources": {"geofabrik": source},
        "files": {
            "tiles": {"key": prefix + tiles.name, "sha256": sha256_file(tiles), "bytes": tiles.stat().st_size},
            "checks": {"key": prefix + checks.name, "sha256": sha256_file(checks)},
        },
        "checks": report,
    }


def local_publish(site: Path, build_id: str, files: dict[str, Path], manifest: dict) -> None:
    """Same layout as R2 under a local folder, for the local end-to-end run (Task 10)."""
    folder = site / build_prefix(build_id)
    folder.mkdir(parents=True, exist_ok=False)
    for path in files.values():
        shutil.copyfile(path, folder / path.name)
    (folder / "LICENSE.md").write_text(LICENSE_MD, encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    latest = latest_document(build_id, manifest["dataDate"], root="routing/")
    (site / LATEST_KEY).parent.mkdir(parents=True, exist_ok=True)
    (site / LATEST_KEY).write_text(json.dumps(latest), encoding="utf-8")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Build the monthly routing graph")
    p.add_argument("--workdir", required=True)
    p.add_argument("--pbf-file", required=True, help="the Geofabrik extract (scripts/fetch-pbf.py, or the workflow's download step)")
    p.add_argument("--pbf-record", help="its download record; required to publish to R2")
    p.add_argument("--port", type=int, default=8002, help="local port of the temporary check service")
    p.add_argument("--skip-publish", action="store_true")
    p.add_argument("--local-publish", help="write the R2 layout to this folder instead")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    publishing = not args.skip_publish and not args.local_publish
    # Same single-writer rule as the search data: only the workflow publishes to R2.
    if publishing and os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("publishing to R2 runs only in the search-data workflow; "
                         "use --skip-publish or --local-publish")
    if publishing and not args.pbf_record:
        raise SystemExit("publishing needs --pbf-record (written with the extract by search.sources)")
    now = datetime.now(timezone.utc)
    build_id = now.strftime("%Y%m%dT%H%M%SZ")
    built_at = now.isoformat(timespec="seconds")

    pbf = Path(args.pbf_file)
    source = load_pbf_record(args.pbf_record, pbf) if args.pbf_record else {"url": None, "sha256": sha256_file(pbf)}
    data_date = source_date(pbf_timestamp(pbf))
    if data_date is None and (publishing or args.local_publish):
        raise CheckFailed("the data time of the Geofabrik extract is unknown")

    tar = build_graph(pbf, workdir / f"graph-{build_id}")
    with serving(tar.parent, args.port) as url:
        client = ValhallaClient(url)
        client.wait_ready()
        problems, report = run_checks(client)
    print(json.dumps({"buildId": build_id, "dataDate": data_date, "checks": report}, ensure_ascii=False))
    if problems:
        raise CheckFailed("; ".join(problems))

    out = workdir / f"out-{build_id}"
    out.mkdir()
    graph_dir = tar.parent  # still holds the generated valhalla.json after the tar moves out
    tiles = out / "tiles.tar"
    shutil.move(tar, tiles)
    checks = out / "checks.json"
    checks.write_text(json.dumps(checks_document(), ensure_ascii=False, indent=1), encoding="utf-8")
    files = {"tiles": tiles, "checks": checks}
    build_config = out / "build-config.json"
    shutil.copyfile(graph_dir / CONFIG_NAME, build_config)
    manifest = build_manifest(build_id, built_at, source, data_date, tiles, checks, build_config, report)
    if publishing:
        check_source_fresh(data_date, built_at)
        client = make_client(os.environ["R2_ACCOUNT_ID"], os.environ["R2_ACCESS_KEY_ID"],
                             os.environ["R2_SECRET_ACCESS_KEY"])
        bucket = os.environ["R2_BUCKET"]
        check_tiles_size(tiles.stat().st_size, previous_tiles_bytes(client, bucket))
        publish_routing(client, bucket, build_id, files, manifest)
    else:
        check_tiles_size(tiles.stat().st_size, None)
        if args.local_publish:
            local_publish(Path(args.local_publish), build_id, files, manifest)
    # Keep the graph until validation and publishing have both succeeded, for diagnosis on failure.
    # The tar and configuration are in out-<buildId>. Root-owned directories on Linux may remain;
    # report that without turning a successful publication into a failure.
    shutil.rmtree(graph_dir, ignore_errors=True)
    if graph_dir.exists():
        print(f"warning: could not fully remove graph folder {graph_dir}", flush=True)
    print(json.dumps({"published": publishing or bool(args.local_publish), "buildId": build_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
