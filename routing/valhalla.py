"""A small Valhalla HTTP client for the graph checks (route C spec §3.3)."""
from __future__ import annotations

import time

import requests

# Route C spec decision 3: motor_scooter avoids motorways and motorroad=yes; 60 km/h instead of the
# default 45 so roads signed 60 or 70 are not timed at 45.
SCOOTER_TOP_SPEED_KPH = 60
COSTING = {
    "scooter": ("motor_scooter", {"motor_scooter": {"top_speed": SCOOTER_TOP_SPEED_KPH}}),
    "car": ("auto", {}),
}
# Valhalla error codes that mean "no route between these points" (app repo research/
# 2026-10-08-osm-routing-and-search/evidence/valhalla-route-api-reference.md, error list).
NO_ROUTE_CODES = frozenset({154, 170, 171, 441, 442})


class NoRoute(Exception):
    pass


class ValhallaError(Exception):
    pass


class ValhallaClient:
    def __init__(self, base_url: str, session=None, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.s = session or requests.Session()
        self.timeout = timeout

    def wait_ready(self, timeout_s: float = 600, sleep=time.sleep, clock=time.monotonic) -> None:
        deadline = clock() + timeout_s
        while True:
            try:
                if self.s.get(f"{self.base}/status", timeout=5).status_code == 200:
                    return
            except requests.RequestException:
                pass
            if clock() >= deadline:
                raise ValhallaError("Valhalla did not become ready")
            sleep(2)

    def _post(self, action: str, body: dict) -> dict:
        r = self.s.post(f"{self.base}/{action}", json=body, timeout=self.timeout)
        if r.status_code == 200:
            return r.json()
        try:
            code = r.json().get("error_code")
        except ValueError:
            code = None
        if r.status_code == 400 and code in NO_ROUTE_CODES:
            raise NoRoute(str(code))
        raise ValhallaError(f"{action} answered {r.status_code} (error_code {code})")

    def route(self, origin, destination, mode: str) -> dict:
        costing, options = COSTING[mode]
        data = self._post("route", {
            "locations": [{"lat": origin[0], "lon": origin[1]}, {"lat": destination[0], "lon": destination[1]}],
            "costing": costing,
            "costing_options": options,
            "directions_type": "none",
            "units": "kilometers",
        })
        trip = data["trip"]
        return {"km": float(trip["summary"]["length"]), "seconds": float(trip["summary"]["time"]),
                "shape": trip["legs"][0]["shape"]}

    def road_classes(self, shape: str, mode: str) -> list[str]:
        """The road class of every edge the route uses; edge_walk is exact for a shape Valhalla returned."""
        costing, options = COSTING[mode]
        data = self._post("trace_attributes", {
            "encoded_polyline": shape,
            "shape_match": "edge_walk",
            "costing": costing,
            "costing_options": options,
            "filters": {"attributes": ["edge.road_class"], "action": "include"},
        })
        return [e.get("road_class") for e in data.get("edges", [])]
