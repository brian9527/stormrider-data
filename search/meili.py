"""Minimal Meilisearch client for the probe check before publishing (backend B spec §3.5)."""
from __future__ import annotations

import gzip
import time

import requests

# Keep in sync with stormrider-server src/search/settings.ts (PLACES_SETTINGS).
PLACES_SETTINGS = {
    "searchableAttributes": ["name", "aliases", "where"],
    "displayedAttributes": ["*"],
    "filterableAttributes": ["_geo"],
    "sortableAttributes": ["_geo"],
    "distinctAttribute": "key",
    "localizedAttributes": [{"attributePatterns": ["name", "aliases", "where"], "locales": ["cmn"]}],
    "rankingRules": ["words", "typo", "proximity", "attribute", "exactness", "sort", "importance:desc"],
}


class MeiliError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(f"meilisearch {status} {code}")
        self.status, self.code = status, code


class Meili:
    def __init__(self, url: str, key: str, session=None):
        self.url = url.rstrip("/")
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {key}"})

    def _call(self, method: str, path: str, timeout: int = 60, **kw):
        r = self.s.request(method, self.url + path, timeout=timeout, **kw)
        body = r.json() if r.content else None
        if r.status_code >= 400:
            raise MeiliError(r.status_code, (body or {}).get("code", "unknown"))
        return body

    def wait(self, task: dict, timeout_s: int = 3600, poll_s: float = 1.0) -> None:
        deadline = time.monotonic() + timeout_s
        while True:
            t = self._call("GET", f"/tasks/{task['taskUid']}")
            if t["status"] == "succeeded":
                return
            if t["status"] in ("failed", "canceled"):
                raise MeiliError(0, (t.get("error") or {}).get("code", "task_failed"))
            if time.monotonic() > deadline:
                raise MeiliError(0, "task_timeout")
            time.sleep(poll_s)

    def recreate_index(self, uid: str, primary_key: str) -> None:
        try:
            self.wait(self._call("DELETE", f"/indexes/{uid}"))
        except MeiliError as e:
            if e.code != "index_not_found":
                raise
        self.wait(self._call("POST", "/indexes", json={"uid": uid, "primaryKey": primary_key}))

    def update_settings(self, uid: str, settings: dict) -> None:
        self.wait(self._call("PATCH", f"/indexes/{uid}/settings", json=settings))

    def add_ndjson_gz(self, uid: str, path, batch_lines: int = 10_000) -> int:
        count, batch = 0, []
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                batch.append(line)
                count += 1
                if len(batch) >= batch_lines:
                    self._send(uid, batch)
                    batch = []
        if batch:
            self._send(uid, batch)
        return count

    def _send(self, uid: str, lines: list[str]) -> None:
        self.wait(self._call("POST", f"/indexes/{uid}/documents", timeout=300,
                             data="\n".join(lines).encode("utf-8"),
                             headers={"Content-Type": "application/x-ndjson"}))

    def search(self, uid: str, q: str, limit: int = 3) -> list[dict]:
        return self._call("POST", f"/indexes/{uid}/search", json={"q": q, "limit": limit, "locales": ["cmn"]})["hits"]
