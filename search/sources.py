"""Download the monthly inputs and check them against the publisher's md5 (backend B spec §3.2)."""
from __future__ import annotations

import hashlib
import re
import time

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
