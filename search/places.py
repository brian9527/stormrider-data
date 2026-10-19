"""Photon dump → place documents for Meilisearch (backend B spec §3.3).

The rules are the M version of the engine comparison (research 2026-10-12, TEST-PLAN §1.1) plus
three additions: same-name roads merged per district, never-a-destination categories removed, and
gzip ndjson output."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass

import zstandard

NAME_EXTRA = ("short_name", "official_name")
WHERE_KEYS = ("county", "city", "suburb")
# osm_key/osm_value pairs that are never a destination (spec §3.3 addition 2). Each new entry must
# first be checked on the dev set (plan Task 11).
EXCLUDED_CATEGORIES = frozenset({("amenity", "parcel_locker")})


@dataclass
class PlaceStats:
    records: int = 0
    # Lines or entries that break the dump format: bad JSON, a missing or malformed header, a Place
    # line whose content is not a list of objects. Task 3 refuses to publish when this is non-zero.
    decode_errors: int = 0
    non_place_lines: int = 0
    excluded_category: int = 0
    skipped: int = 0
    roads_merged_away: int = 0
    documents: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def split_names(value) -> list[str]:
    return [x.strip() for x in value.split(";") if x.strip()] if isinstance(value, str) else []


def where_of(record: dict) -> str:
    addr = record.get("address") if isinstance(record.get("address"), dict) else {}
    return " ".join(addr[k].strip() for k in WHERE_KEYS if isinstance(addr.get(k), str) and addr[k].strip())


def dump_key(record: dict) -> str | None:
    t, i = record.get("object_type"), record.get("object_id")
    return f"{t}{i}" if t is not None and i is not None else None


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def make_doc(record: dict, n: int) -> dict | None:
    """One dump content record → one document, or None (no OSM id, no name or no centroid)."""
    names = record.get("name") if isinstance(record.get("name"), dict) else {}
    name = names.get("name")
    centroid = record.get("centroid")
    key = dump_key(record)
    if not key or not isinstance(name, str) or not name.strip():
        return None
    if not (isinstance(centroid, list) and len(centroid) == 2 and all(_finite(v) for v in centroid)):
        return None
    lon, lat = centroid
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    aliases: list[str] = []
    for k, v in names.items():
        if k != "name":
            aliases += split_names(v)
    for fld in NAME_EXTRA:
        aliases += split_names(extra.get(fld))
    wikidata = extra.get("wikidata")
    importance = record.get("importance")
    return {
        "doc_id": f"{key}-{n}",
        "key": key,
        "name": name,
        "aliases": sorted({a for a in aliases if a != name}),
        "where": where_of(record),
        "osm_key": record.get("osm_key"),
        "osm_value": record.get("osm_value"),
        "wikidata": wikidata if isinstance(wikidata, str) and wikidata else None,
        "importance": float(importance) if _finite(importance) else 0.0,
        "_geo": {"lat": lat, "lng": lon},
    }


def _scalars(obj: dict, prefix: str = "") -> dict:
    return {f"{prefix}{k}": v for k, v in obj.items()
            if isinstance(v, (str, int, float, bool)) or v is None}


class _ZstdFrames(io.RawIOBase):
    """Decompresses every zstd frame of `source` and raises at the end unless the last frame was
    complete. zstandard's stream_reader stops quietly when the input ends mid-frame, so a cut-off
    file (for example a local copy that was never md5-checked) could otherwise look clean.

    decompressobj() returns everything one input chunk yields, so the chunk size is what bounds
    memory: a zstd block decodes to at most 128 KiB and takes at least 4 bytes, so 2 KiB of input
    yields at most about 64 MiB even for pathological data (about 110 KiB at the dump's ~55:1)."""

    def __init__(self, source, read_size: int = 2 << 10):
        self._source, self._read_size = source, read_size
        self._dctx = zstandard.ZstdDecompressor()
        self._frame = self._dctx.decompressobj()
        self._fed = False
        self._out, self._pos = b"", 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        while self._pos >= len(self._out):
            data = self._source.read(self._read_size)
            if not data:
                if self._fed and not self._frame.eof:
                    raise zstandard.ZstdError("the zstd input ends in the middle of a frame")
                return 0
            self._out, self._pos = self._decompress(data), 0
        n = min(len(buffer), len(self._out) - self._pos)
        buffer[:n] = self._out[self._pos:self._pos + n]
        self._pos += n
        return n

    def _decompress(self, data: bytes) -> bytes:
        out = []
        while data:
            if self._frame.eof:  # the previous frame is done; the rest starts the next one
                self._frame = self._dctx.decompressobj()
            self._fed = True
            out.append(self._frame.decompress(data))
            data = self._frame.unused_data if self._frame.eof else b""
        return b"".join(out)


def _is_header(parsed) -> bool:
    # Photon JSON dump format 0.1.0 §2–3: the first object is a NominatimDumpFile with an object content.
    return isinstance(parsed, dict) and parsed.get("type") == "NominatimDumpFile" and isinstance(parsed.get("content"), dict)


def iter_dump_records(binary_stream, stats: PlaceStats, header: dict) -> Iterator[dict]:
    """Yield the content records of every Place line of a zstd-compressed Photon dump. The first
    line must be the dump header; its scalar fields (and those of its `content`) go into `header`.
    Anything that breaks the format is counted in stats.decode_errors rather than skipped quietly."""
    raw = io.BufferedReader(_ZstdFrames(binary_stream), buffer_size=1 << 20)
    text = io.TextIOWrapper(raw, encoding="utf-8")
    lines = 0
    for n, line in enumerate(text):
        lines += 1
        try:
            parsed = json.loads(line)
        except ValueError:
            stats.decode_errors += 1
            continue
        if n == 0:
            if _is_header(parsed):
                header.update(_scalars(parsed))
                header.update(_scalars(parsed["content"], "content."))
            else:
                # Without the header the data time is unknown too; the line itself is not used.
                stats.decode_errors += 1
            continue
        if not isinstance(parsed, dict) or not isinstance(parsed.get("type"), str):
            # Every object in the format has a type; anything else is a broken line.
            stats.decode_errors += 1
            continue
        if parsed["type"] != "Place":
            # Other typed objects (CountryInfo, extensions) are allowed and not needed here.
            stats.non_place_lines += 1
            continue
        content = parsed.get("content")
        if not isinstance(content, list):
            stats.decode_errors += 1
            continue
        for c in content:
            if isinstance(c, dict):
                yield c
            else:
                stats.decode_errors += 1
    if lines == 0:
        stats.decode_errors += 1  # no text at all, so no header either


def _merge_road(a: dict, b: dict) -> dict:
    keep, other = (b, a) if b["importance"] > a["importance"] else (a, b)
    merged = dict(keep)
    merged["aliases"] = sorted(set(keep["aliases"]) | set(other["aliases"]))
    return merged


def build_documents(records: Iterable[dict], stats: PlaceStats, *, merge_roads: bool = True,
                    exclude: bool = True) -> Iterator[dict]:
    """Documents in dump order; merged roads come last. `merge_roads` and `exclude` are switched
    off only for the dev-set comparison (plan Task 11)."""
    roads: dict[tuple[str, str], dict] = {}
    n = 0
    for record in records:
        stats.records += 1
        if exclude and (record.get("osm_key"), record.get("osm_value")) in EXCLUDED_CATEGORIES:
            stats.excluded_category += 1
            continue
        doc = make_doc(record, n)
        if doc is None:
            stats.skipped += 1
            continue
        n += 1
        if merge_roads and doc["osm_key"] == "highway" and doc["where"]:
            k = (doc["name"], doc["where"])
            if k in roads:
                roads[k] = _merge_road(roads[k], doc)
                stats.roads_merged_away += 1
            else:
                roads[k] = doc
            continue
        stats.documents += 1
        yield doc
    for doc in roads.values():
        stats.documents += 1
        yield doc


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_ndjson_gz(docs: Iterable[dict], path) -> tuple[int, str]:
    """Write docs as gzip ndjson; return (count, sha256 of the gzip file)."""
    count = 0
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as f:
        for d in docs:
            # allow_nan=False: one NaN would make Meilisearch reject the whole batch.
            f.write(json.dumps(d, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
            count += 1
    return count, sha256_file(path)
