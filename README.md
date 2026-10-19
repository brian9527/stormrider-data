# stormrider-data

GFS → PNG pipeline for the Stormrider weather globe.
Runs on GitHub Actions cron; publishes to Cloudflare R2.

Design spec: weather-app repo, `docs/superpowers/specs/2026-07-18-gfs-data-pipeline-design.md`.

## Local dev

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    pytest

## Manual run

    R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=stormrider-weather \
      python -m pipeline.main

## Search data (monthly)

`search/` builds the place-search data for StormRider (backend B spec, app repo
`docs/superpowers/specs/2026-10-18-backend-b-search-design.md`).

- Workflow: `.github/workflows/search-data.yml`, UTC 1st of each month 18:30, or run it by hand.
- Inputs: GraphHopper's Photon dump for Taiwan and the Geofabrik Taiwan extract (md5-checked).
- Output on R2 under `search/<buildId>/` (UTC time of the run; a published build is never
  overwritten): `places.ndjson.gz`, `nearby.ndjson.gz`, `probes.json`, `manifest.json`, `LICENSE.md`.
  `search/latest.json` points at the newest. Three builds are kept.
- `dataDate` in the manifest is the older of the two inputs' data times; publishing stops when either
  time cannot be read or the older one is more than 30 days old.
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
