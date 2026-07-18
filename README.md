# stormrider-weather-data

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
