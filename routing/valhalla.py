"""A small Valhalla HTTP client for the graph checks (route C spec §3.3)."""
from __future__ import annotations

import math
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
        try:
            r = self.s.post(f"{self.base}/{action}", json=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise ValhallaError(f"{action} request failed ({type(e).__name__})") from e
        try:
            data = r.json()
        except ValueError as e:
            raise ValhallaError(f"{action} answered {r.status_code} with invalid JSON") from e
        if not isinstance(data, dict):
            raise ValhallaError(f"{action} answered {r.status_code} with a non-object response")
        if r.status_code == 200:
            return data
        code = data.get("error_code")
        if r.status_code == 400 and type(code) is int and code in NO_ROUTE_CODES:
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
        try:
            trip = data["trip"]
            km, seconds = trip["summary"]["length"], trip["summary"]["time"]
            legs = trip["legs"]
            # These checks use two distinct endpoints, so there must be exactly one nonempty leg.
            if not isinstance(legs, list) or len(legs) != 1:
                raise ValueError("expected one leg")
            shape = legs[0]["shape"]
            if not isinstance(shape, str) or not shape:
                raise ValueError("missing shape")
            if any(type(n) not in (int, float) or not math.isfinite(n) or n <= 0 for n in (km, seconds)):
                raise ValueError("invalid distance or duration")
        except (KeyError, TypeError, ValueError, OverflowError) as e:
            raise ValhallaError("route returned a malformed trip") from e
        return {"km": float(km), "seconds": float(seconds), "shape": shape}

    def road_classes(self, shape: str, mode: str) -> list[str | None]:
        """Walk the original route shape on the same graph; a failed match must fail the check."""
        costing, options = COSTING[mode]
        data = self._post("trace_attributes", {
            "encoded_polyline": shape,
            "shape_match": "edge_walk",
            "costing": costing,
            "costing_options": options,
            "filters": {"attributes": ["edge.road_class"], "action": "include"},
        })
        edges = data.get("edges", [])
        if not isinstance(edges, list) or any(not isinstance(e, dict) for e in edges):
            raise ValhallaError("trace_attributes returned malformed edges")
        classes = [e.get("road_class") for e in edges]
        if any(c is not None and not isinstance(c, str) for c in classes):
            raise ValhallaError("trace_attributes returned a malformed road class")
        return classes
