"""Checks on a freshly built graph before it is published (route C spec §3.3). The server runs the
same routes from the published checks.json."""
from __future__ import annotations

from routing.valhalla import SCOOTER_TOP_SPEED_KPH, NoRoute, ValhallaError

# Endpoints of the 2026-10-09 local test (app repo research/2026-10-08-osm-routing-and-search/
# results/endpoints.json). Bands: 0.7 × the shorter and 1.35 × the longer of the scooter and car
# distances measured then (results/routes-summary.json; Valhalla 3.9.1, scooter at 45 km/h),
# rounded outwards to 0.1 km. Wide on purpose: the check catches a broken graph, not a new detour.
CHECK_ROUTES = [
    {"name": "高雄車站→旗山老街", "from": [22.63944, 120.30402], "to": [22.88745, 120.48206], "km": [30.5, 59.8]},
    {"name": "台北車站→高鐵桃園站", "from": [25.04624, 121.51747], "to": [25.01372, 121.21414], "km": [26.1, 50.5]},
    {"name": "新竹車站→竹東車站", "from": [24.80158, 120.97161], "to": [24.73825, 121.09469], "km": [11.1, 23.3]},
    {"name": "台中車站→鹿港老街", "from": [24.13711, 120.68687], "to": [24.05634, 120.43261], "km": [23.0, 60.7]},
    {"name": "板橋車站→八里渡船頭", "from": [25.01371, 121.46426], "to": [25.15929, 121.43588], "km": [13.9, 28.1]},
    {"name": "台北車站→南港車站", "from": [25.04624, 121.51747], "to": [25.05288, 121.60704], "km": [7.8, 16.1]},
]
MODES = ("scooter", "car")
# Valhalla's road classes (trace_attributes reference, research evidence valhalla-map-matching.md).
KNOWN_ROAD_CLASSES = frozenset({"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
                                "residential", "service_other"})
# 80% of the 2026-10-09 local build's 310 MB tile tar, rounded down. Task 4 confirms it against the
# first real build and lowers it if that build is smaller.
TILES_FLOOR_BYTES = 240_000_000
MAX_DROP = 0.15


class CheckFailed(Exception):
    pass


def checks_document() -> dict:
    return {"version": 1, "scooterTopSpeedKph": SCOOTER_TOP_SPEED_KPH, "routes": CHECK_ROUTES}


def check_route(client, check: dict, mode: str) -> tuple[list[str], dict]:
    """Problems with one route in one mode (empty when it passes) and what was measured."""
    try:
        trip = client.route(check["from"], check["to"], mode)
    except NoRoute as e:
        return [f"{check['name']} ({mode}): no route ({e})"], {}
    except ValhallaError as e:
        return [f"{check['name']} ({mode}): route failed ({e})"], {}
    problems = []
    lo, hi = check["km"]
    if not lo <= trip["km"] <= hi:
        problems.append(f"{check['name']} ({mode}): {trip['km']:.1f} km outside {lo}–{hi}")
    measured = {"km": round(trip["km"], 3), "minutes": round(trip["seconds"] / 60, 1)}
    if mode == "scooter":
        try:
            classes = client.road_classes(trip["shape"], mode)
        except (NoRoute, ValhallaError) as e:
            problems.append(f"{check['name']} ({mode}): road class lookup failed ({e})")
            return problems, measured
        measured["motorwayEdges"] = sum(1 for c in classes if c == "motorway")
        unknown = sum(1 for c in classes if c not in KNOWN_ROAD_CLASSES)
        if not classes:
            problems.append(f"{check['name']} ({mode}): no edges matched the route")
        elif unknown:
            problems.append(f"{check['name']} ({mode}): {unknown} edges without a known road class")
        elif measured["motorwayEdges"]:
            problems.append(f"{check['name']} ({mode}): uses {measured['motorwayEdges']} motorway edges")
    return problems, measured


def run_checks(client, checks=CHECK_ROUTES) -> tuple[list[str], list[dict]]:
    problems, report = [], []
    for check in checks:
        for mode in MODES:
            found, measured = check_route(client, check, mode)
            problems += found
            report.append({"name": check["name"], "mode": mode, **measured})
    return problems, report


def check_tiles_size(size: int, previous: int | None) -> None:
    floor = TILES_FLOOR_BYTES if previous is None else int(previous * (1 - MAX_DROP))
    if size < floor:
        raise CheckFailed(f"tiles.tar is {size} bytes, below {floor}")
