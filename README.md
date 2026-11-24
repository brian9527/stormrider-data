# stormrider-data

GFS → PNG pipeline for the Stormrider weather globe.
Runs on GitHub Actions cron; publishes to Cloudflare R2.

Design spec: weather-app repo, `docs/superpowers/specs/2026-07-18-gfs-data-pipeline-design.md`.

## Local dev

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    pytest

The lock files are resolved for the workflows' Linux / Python 3.12 and allow wheels only. If they
do not install on another local Python, use `requirements.in` / `requirements-search.in` instead;
that gives up the pinned versions and hash checks.

## Dependencies

The workflows run with the R2 upload keys, so the Python packages they install and the code of the
actions they use are pinned.

- `requirements.in`, `requirements-search.in`: direct dependencies only. Edit these.
- `requirements.txt`, `requirements-search.txt`: generated lock files. Every package, including
  indirect ones, is pinned to one version with the hashes of its files, and the file itself says
  `--only-binary :all:`. The workflows install them with `--require-hashes --only-binary=:all:`,
  so a file whose hash does not match, an unpinned package, or a build from source makes the
  install fail.
- When a lock file is generated, only files uploaded at least 14 days ago are considered, so a
  malicious release has time to be caught before it is locked in. This does not re-check versions
  that are already locked.
- `scripts/lock-tools.in` / `scripts/lock-tools.txt`: pip and pip-tools for the lock script, locked
  and installed the same strict way.
- GitHub Actions are pinned to a commit, with the version in a comment.
- Not pinned: the runner image, its system libraries, the Python patch release and pip that
  setup-python provides, and the `gh` CLI in the keepalive job (which has no R2 keys).

To update (Docker must be running; both scripts use the same pinned `python:3.12-slim` image):

    scripts/lock-requirements.sh                         # after editing a .in file
    scripts/lock-requirements.sh --upgrade               # or move everything to the newest allowed versions
    scripts/lock-requirements.sh --upgrade-package numpy # or one package (its dependencies may move too)
    scripts/check-locked-install.sh                      # install like the workflows and run the tests

`check-locked-install.sh` prints the environment it ran in; it must say Linux x86_64 and Python 3.12.
Commit the `.in` and `.txt` files together, then confirm the next workflow run on GitHub, which is
the real Ubuntu environment. To update an action, look up the commit of the new release tag,
replace the SHA in both workflows and update the comment.

## Manual run

    R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=stormrider-weather \
      python -m pipeline.main

## Search data (monthly)

`search/` builds the place-search data for StormRider (backend B spec, app repo
`docs/superpowers/specs/2026-10-18-backend-b-search-design.md`).

- Workflow: `.github/workflows/search-data.yml`, UTC 1st of each month 18:30, or run it by hand.
- Inputs: GraphHopper's Photon dump for Taiwan and the Geofabrik Taiwan extract (md5-checked). In
  the workflow the extract comes from the download step shared with the routing graph (below).
- Output on R2 under `search/<buildId>/` (UTC time of the run; a published build is never
  overwritten): `places.ndjson.gz`, `nearby.ndjson.gz`, `probes.json`, `manifest.json`, `LICENSE.md`.
  `search/latest.json` points at the newest. Three builds are kept.
- `dataDate` in the manifest is the older of the two inputs' data times; publishing stops when either
  time cannot be read, the older one is more than 30 days old, or one is dated more than a day after
  the build (a clock or source error).
- Only this workflow publishes to R2 (one run at a time); a build folder that already holds anything
  is never written to again.
- If GitHub disables the schedule after 60 days without commits, re-enable it on the Actions page.
  The server's `/health` alerts when its data is more than 45 days old.
- Data © OpenStreetMap contributors, ODbL 1.0. The published files are the derived database (ODbL §4.6).

Local run (needs Docker for the probe check):

    .venv/bin/pip install -r requirements-search.txt
    docker run -d --rm --name meili-check -p 127.0.0.1:7700:7700 \
      -e MEILI_MASTER_KEY=local-check-key-0001 -e MEILI_NO_ANALYTICS=true \
      getmeili/meilisearch@sha256:0bf32debcbfa8ba4e418679025f4935884972171513c92f0584689ff994a61df
    MEILI_MASTER_KEY=local-check-key-0001 .venv/bin/python -I -B scripts/build-search.py \
      --workdir .build/search --skip-publish

## Routing graph (monthly)

`routing/` builds the Valhalla routing graph for StormRider (route C spec, app repo
`docs/superpowers/specs/2026-11-18-route-c-valhalla-design.md`).

- Same workflow and schedule as the search data, from the same Geofabrik download
  (`python -m search.sources` writes the extract and its md5-checked record once). The routing step
  runs even when the search step fails or times out, provided the download succeeded and the job
  has not been cancelled or reached its time limit; either failing fails the run. Meilisearch is
  stopped before routing so unfinished indexing cannot compete with the graph build.
- Built with Valhalla 3.9.1, `ghcr.io/valhalla/valhalla-scripted` pinned by digest in
  `routing/build.py`; no network during the build; no administrative, time-zone or elevation data.
- Before publishing to R2: six fixed check routes (`routing/checks.py`) must route in both modes within
  their distance bands; every edge of a scooter route must have a known road class and none may be a
  motorway; a Valhalla error or a malformed answer counts as a failed check. The tile tar must not
  shrink by more than 15% (first build: at least 240 MB; a published build whose size cannot be read
  stops the publish rather than counting as a first build), and the extract must be at most 30 days
  old and not dated more than a day after the build.
- A run that fails after building keeps its `graph-<buildId>` folder for diagnosis; artifacts already
  staged for publishing are in `out-<buildId>`. After success, graph removal is best effort: root-owned
  directories may remain on Linux, with a warning (the hosted runner is discarded after the job).
  Containers are named after the graph folder; Python errors and Ctrl-C attempt removal with a
  60-second timeout. SIGTERM/SIGKILL or Docker failures can leave a container behind. On a local
  machine, inspect `docker ps -a --filter name=sr-valhalla-` and remove the affected container with
  `docker rm -f <container-name>`; an orphaned check service can still occupy port 8002.
- Output on R2 under `routing/<buildId>/`: `tiles.tar`, `checks.json`, `manifest.json` (data date,
  extract checksums, Valhalla image digest, hash of the generated build settings, file checksums,
  check results), `LICENSE.md`.
  `routing/latest.json` points at the newest. Six builds (about six months) are kept, so the server
  can be pinned back that far (`ROUTING_PIN_BUILD`, server runbook §14).
- The search step has a 100-minute limit within the 150-minute job budget; setup and download also
  use that budget, so this does not guarantee a full 50 minutes for routing.
- Data © OpenStreetMap contributors, ODbL 1.0. `tiles.tar` is the derived database (ODbL §4.6); this
  repo is how it was made: the image digest, the build settings in `routing/build.py` and the checks.
- The server's routing speeds are set in `stormrider-server`; the scooter cap here (60 km/h) is only
  used by the checks.

Local run (needs Docker and the dependencies in `requirements-search.txt`). Local publishing checks
routes and the 240 MB floor, but does not enforce the 30-day age limit or compare with the last R2 build:

    .venv/bin/python -I -B scripts/fetch-pbf.py --pbf-out .build/osm/taiwan-latest.osm.pbf --record .build/osm/pbf.json
    .venv/bin/python -I -B scripts/build-routing.py --workdir .build/routing \
      --pbf-file .build/osm/taiwan-latest.osm.pbf --pbf-record .build/osm/pbf.json --local-publish .build/site

## Map styles (monthly)

The two map styles the app uses, OpenFreeMap's Liberty (light) and Fiord (dark) with Chinese labels
(D1 spec in the app repo, `docs/superpowers/specs/2026-11-21-d1-maplibre-design.md`).

- `mapstyle/transform.py` turns every label that reads a name field into `name:zh-Hant`, else `name`,
  and puts the credit, every upstream style and the licence notices (`mapstyle/notices/`, copied
  verbatim from the app repo's licence research) into `metadata.stormrider:attribution`.
- `mapstyle/checks.py` must pass before anything is published: version 8, every source, glyphs and
  sprite on `tiles.openfreemap.org`, layers with unique ids on sources that exist, the key labels of each
  style in Chinese and none left showing a latin name, Noto Sans on every text layer, a complete
  attribution, at most 512 KB. It holds back what the app's `isMapStyle` would refuse
  (`src/services/mapStyle/validate.ts` in the app repo); keep the two in step. An upstream that renames a
  key label layer or redirects stops the publish until someone looks; phones keep the style they have.
- The weather workflow re-enables this one every six hours (keepalive), so disabling it on the Actions page
  does not last; to pause publishing, also remove that step from `.github/workflows/update.yml`.
- The `update-map-styles` workflow runs monthly (UTC day 1, 19:00) and on demand. It publishes to R2
  under `map/<buildId>/` (`liberty.json`, `fiord.json`, `manifest.json`, `LICENSE.md`; six builds
  kept, `map/latest.json` written last) only when the output differs from the latest build.
- Local, without R2: `.venv/bin/python -I -B scripts/build-map-styles.py --out .build/map-styles`.
  The app bundles that output in `src/assets/map-styles/`.
