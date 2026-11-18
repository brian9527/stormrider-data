"""Download the monthly inputs and check them against the publisher's md5 (backend B spec §3.2).

`main` downloads the Geofabrik extract once for both monthly builds, search and routing (route C
spec §3.1); run it with scripts/fetch-pbf.py locally, or as the module search.sources in the workflow."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import requests

PHOTON_URL = "https://download1.graphhopper.com/public/asia/taiwan/photon-dump-taiwan-1.0-latest.jsonl.zst"
GEOFABRIK_URL = "https://download.geofabrik.de/asia/taiwan-latest.osm.pbf"
HEADERS = {"User-Agent": "stormrider-data search build (+https://github.com/brian9527/stormrider-data)"}


class ChecksumError(Exception):
    pass


# The monthly run tries a download this many times (main.py); a transient failure would otherwise
# cost a manual rerun.
DOWNLOAD_ATTEMPTS = 3
RETRY_WAIT_S = (60, 300)


def _retryable(e: Exception) -> bool:
    """Network trouble, a server-side or rate-limit status, or a checksum mismatch (the file may have
    been replaced while it was downloading). A missing file (404) and the like are not retried."""
    if isinstance(e, requests.HTTPError):
        status = e.response.status_code if e.response is not None else None
        return status is None or status == 429 or status >= 500
    return isinstance(e, (requests.RequestException, ChecksumError))


def download(url: str, dest, session=None, timeout: int = 60, attempts: int = 1, sleep=time.sleep) -> dict:
    """Stream `url` to `dest` and verify it against `url + '.md5'`. Returns url, sha256, md5, bytes.
    With attempts > 1, a retryable failure repeats the whole download, md5 included, after a pause."""
    s = session or requests.Session()
    for attempt in range(1, attempts + 1):
        try:
            return _download_once(url, dest, s, timeout)
        except Exception as e:
            if attempt == attempts or not _retryable(e):
                raise
            print(f"download of {url} failed ({type(e).__name__}); trying again", flush=True)
            sleep(RETRY_WAIT_S[min(attempt, len(RETRY_WAIT_S)) - 1])
    raise AssertionError("unreachable")


def _download_once(url: str, dest, s, timeout: int) -> dict:
    md5_response = s.get(url + ".md5", timeout=timeout, headers=HEADERS)
    md5_response.raise_for_status()
    fields = md5_response.text.split()  # "<md5>  <filename>"
    expected = fields[0].lower() if fields else ""
    if not re.fullmatch(r"[0-9a-f]{32}", expected):
        raise ChecksumError(f"unreadable md5 file for {url}")
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    with s.get(url, stream=True, timeout=timeout, headers=HEADERS) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                sha.update(chunk)
                md5.update(chunk)
                size += len(chunk)
    if md5.hexdigest() != expected:
        raise ChecksumError(f"md5 mismatch for {url}")
    return {"url": url, "sha256": sha.hexdigest(), "md5": md5.hexdigest(), "bytes": size}


def load_pbf_record(record_path, pbf_path) -> dict:
    """The download record `main` wrote, checked against the file it describes: the manifests keep
    the publisher's URL and md5, so a record for some other file must not stand in for it."""
    try:
        record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ChecksumError(f"unreadable download record {record_path}") from e
    if (not isinstance(record, dict) or record.get("url") != GEOFABRIK_URL
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{32}", str(record.get("md5", "")))
            or type(record.get("bytes")) is not int):
        raise ChecksumError(f"invalid download record {record_path}")
    sha = hashlib.sha256()
    with open(pbf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    if sha.hexdigest() != record["sha256"]:
        raise ChecksumError(f"{pbf_path} does not match its download record")
    return {k: record[k] for k in ("url", "sha256", "md5", "bytes")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Download the Geofabrik Taiwan extract once for the monthly builds")
    p.add_argument("--pbf-out", required=True)
    p.add_argument("--record", required=True)
    args = p.parse_args(argv)
    out = Path(args.pbf_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    record = download(GEOFABRIK_URL, out, attempts=DOWNLOAD_ATTEMPTS)
    Path(args.record).write_text(json.dumps(record), encoding="utf-8")
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
