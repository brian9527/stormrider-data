"""Parking and stations near a destination (backend B spec §3.4), from a Geofabrik OSM extract.

Nodes, closed or open ways, and multipolygon relations all count, with or without a name. A way or
an area becomes the average of its (outer) node locations."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import osmium

PARKING_EXCLUDED = (("access", "private"), ("access", "no"), ("motorcycle", "no"))
RAILWAY_STATIONS = frozenset({"station", "halt", "tram_stop"})
STATION_MODES = ("train", "subway", "light_rail")


def classify(tags) -> str | None:
    """`tags` is an osmium TagList or a dict; both have .get()."""
    amenity = tags.get("amenity")
    if amenity == "motorcycle_parking":
        return "motorcycle_parking"
    if amenity == "parking":
        if any(tags.get(k) == v for k, v in PARKING_EXCLUDED):
            return None
        return "parking"
    if tags.get("railway") in RAILWAY_STATIONS:
        return "station"
    if tags.get("public_transport") == "station" and (
        any(tags.get(mode) == "yes" for mode in STATION_MODES) or tags.get("station") in STATION_MODES
    ):
        return "station"
    return None


@dataclass
class NearbyStats:
    nodes: int = 0
    ways: int = 0
    relations: int = 0
    without_locations: int = 0
    documents: int = 0
    by_kind: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _item(osm_id: str, kind: str, name, lat: float, lon: float) -> dict:
    clean = name.strip() if isinstance(name, str) and name.strip() else None
    return {"id": osm_id, "kind": kind, "name": clean, "_geo": {"lat": round(lat, 7), "lng": round(lon, 7)}}


def _ring_points(node_refs) -> list[tuple[float, float]]:
    """Valid locations of one way or ring, without the closing node that repeats the first."""
    points = [(n.location.lat, n.location.lon) for n in node_refs if n.location.valid()]
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    return points


def _average(points: list[tuple[float, float]]) -> tuple[float, float]:
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


class _Collector(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.items: list[dict] = []
        self.stats = NearbyStats()

    def node(self, n):
        kind = classify(n.tags)
        if kind is None or not n.location.valid():
            return
        self.stats.nodes += 1
        self.items.append(_item(f"N{n.id}", kind, n.tags.get("name"), n.location.lat, n.location.lon))

    def way(self, w):
        kind = classify(w.tags)
        if kind is None:
            return
        points = _ring_points(w.nodes)
        if not points:
            self.stats.without_locations += 1
            return
        self.stats.ways += 1
        lat, lon = _average(points)
        self.items.append(_item(f"W{w.id}", kind, w.tags.get("name"), lat, lon))

    def area(self, a):
        # Closed ways also arrive here as areas; way() has already taken them.
        if a.from_way():
            return
        kind = classify(a.tags)
        if kind is None:
            return
        # Ring by ring, so each outer ring drops its own closing node before they are combined.
        points = [p for ring in a.outer_rings() for p in _ring_points(ring)]
        if not points:
            self.stats.without_locations += 1
            return
        self.stats.relations += 1
        lat, lon = _average(points)
        self.items.append(_item(f"R{a.orig_id()}", kind, a.tags.get("name"), lat, lon))


def extract_nearby(path) -> tuple[list[dict], NearbyStats]:
    collector = _Collector()
    collector.apply_file(str(path), locations=True)
    counts: dict[str, int] = {}
    for item in collector.items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    collector.stats.documents = len(collector.items)
    collector.stats.by_kind = counts
    return collector.items, collector.stats


def pbf_timestamp(path) -> str | None:
    """Geofabrik writes the data time into the file header (spec B §3.2); None when absent."""
    reader = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING)
    try:
        value = reader.header().get("osmosis_replication_timestamp")
    finally:
        reader.close()
    return value or None
