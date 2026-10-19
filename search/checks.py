"""Checks before publishing (backend B spec §3.5)."""
from __future__ import annotations

import math
from datetime import date, datetime

# First-publish floors: 80% of the first real build, rounded down to the thousand (local run
# 2026-10-19, build 20261019T031636Z: 525,789 places and 19,367 nearby candidates).
PLACES_FLOOR = 420_000
NEARBY_FLOOR = 15_000
MAX_DROP = 0.10
PROBE_PASS_RATIO = 0.9
# The build date says nothing about how old the inputs are; a stalled upstream would otherwise look
# fresh every month. The server alerts at 45 days (spec §4.3), so refuse to publish anything near that.
MAX_SOURCE_AGE_DAYS = 30

# Dev-set questions the plain Meilisearch (M) answered first in the engine comparison
# (research 2026-10-12, score-dev.json). A result passes when, within the top 3, its name or an
# alias contains one of `names` and it lies within `radius_m` of `ref` ([lat, lon]).
# The server reads the same list from the published probes.json.
PROBES = [
    {"query": "台北車站", "names": ["臺北車站", "台北車站"], "ref": [25.0478, 121.517], "radius_m": 1500},
    {"query": "宜蘭火車站", "names": ["宜蘭車站", "宜蘭火車站"], "ref": [24.7541, 121.758], "radius_m": 1500},
    {"query": "西門町", "names": ["西門町"], "ref": [25.0435, 121.5072], "radius_m": 1500},
    {"query": "逢甲夜市", "names": ["逢甲夜市"], "ref": [24.1778, 120.6462], "radius_m": 1500},
    {"query": "駁二", "names": ["駁二"], "ref": [22.6198, 120.2821], "radius_m": 1500},
    {"query": "日月潭", "names": ["日月潭"], "ref": [23.858, 120.915], "radius_m": 8000},
    {"query": "九份老街", "names": ["九份老街", "基山街"], "ref": [25.1089, 121.8445], "radius_m": 1500},
    {"query": "墾丁大街", "names": ["墾丁大街", "墾丁"], "ref": [21.9454, 120.798], "radius_m": 1500},
    {"query": "高鐵台中站", "names": ["高鐵臺中", "高鐵台中"], "ref": [24.1109, 120.6153], "radius_m": 1500},
    {"query": "臺北市政府", "names": ["臺北市政府", "台北市政府"], "ref": [25.0375, 121.5644], "radius_m": 1200},
]


class CheckFailed(Exception):
    pass


def _as_date(value) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def source_date(*timestamps) -> str | None:
    """The older of the inputs' data times, as YYYY-MM-DD. None unless every one can be read:
    an unreadable time could be arbitrarily old, so it must not simply drop out of the minimum."""
    dates = [_as_date(t) for t in timestamps]
    if not dates or any(d is None for d in dates):
        return None
    return min(dates).isoformat()


def check_source_fresh(data_date: str | None, built_at: str, max_age_days: int = MAX_SOURCE_AGE_DAYS) -> None:
    built = _as_date(built_at)
    if data_date is None or built is None:
        raise CheckFailed("the data time of the inputs is unknown")
    age = (built - date.fromisoformat(data_date)).days
    if age > max_age_days:
        raise CheckFailed(f"the inputs are {age} days old")


def check_counts(places: int, nearby: int, previous: dict | None) -> None:
    if previous is None:
        if places < PLACES_FLOOR:
            raise CheckFailed(f"places {places} below the first-publish floor {PLACES_FLOOR}")
        if nearby < NEARBY_FLOOR:
            raise CheckFailed(f"nearby {nearby} below the first-publish floor {NEARBY_FLOOR}")
        return
    for name, now in (("places", places), ("nearby", nearby)):
        before = previous.get(name)
        if isinstance(before, int) and before > 0 and now < before * (1 - MAX_DROP):
            raise CheckFailed(f"{name} dropped from {before} to {now}")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def probe_passes(probe: dict, hits: list[dict]) -> bool:
    for hit in hits[:3]:
        g = hit.get("_geo") or {}
        lat, lng = g.get("lat"), g.get("lng")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        names = [hit.get("name")] + list(hit.get("aliases") or [])
        named = any(isinstance(n, str) and any(w in n for w in probe["names"]) for n in names)
        if named and haversine_m(lat, lng, probe["ref"][0], probe["ref"][1]) <= probe["radius_m"]:
            return True
    return False


def run_probes(search_fn) -> tuple[int, int, list[str]]:
    failed = [p["query"] for p in PROBES if not probe_passes(p, search_fn(p["query"]))]
    return len(PROBES) - len(failed), len(PROBES), failed


def require_probes(passed: int, total: int) -> None:
    if passed < math.ceil(total * PROBE_PASS_RATIO):
        raise CheckFailed(f"probes passed {passed}/{total}")
