"""Checks a map style must pass before it is published (D1 spec §2.1 item 3)."""
from __future__ import annotations

import json

from mapstyle.transform import ATTRIBUTION_KEY, attribution

OFM = "https://tiles.openfreemap.org/"
PLANET = "https://tiles.openfreemap.org/planet"
MAX_BYTES = 512 * 1024
LATIN_FIELDS = ("name:latin", "name_en", "name:en", "name_int", "name:nonlatin")
LAYER_FIELDS = ("work", "author", "url", "license", "licenseUrl")
SOURCE_TYPES = ("vector", "raster")


class StyleCheckFailed(Exception):
    pass


def _urls(value) -> list:
    """glyphs and sprite are a URL; sprite may also be a list of {id, url}."""
    if isinstance(value, list):
        return [v.get("url") if isinstance(v, dict) else v for v in value]
    return [value]


def _attribution_problems(meta) -> list[str]:
    if not isinstance(meta, dict):
        return ["attribution missing"]
    problems = []
    if not meta.get("credit") or not meta.get("notices") or not meta.get("layers"):
        problems.append("attribution incomplete")
    for layer in meta.get("layers") or []:
        if not all(isinstance(layer.get(k), str) and layer.get(k) for k in LAYER_FIELDS):
            problems.append("attribution layer incomplete")
    for notice in meta.get("notices") or []:
        if not (isinstance(notice.get("text"), str) and notice["text"].strip()):
            problems.append("attribution notice empty")
    return problems


def check_style(name: str, style: dict, changed: int, encoded: bytes) -> None:
    problems = []
    if style.get("version") != 8:
        problems.append("version is not 8")
    layers = style.get("layers")
    if not isinstance(layers, list) or not layers:
        problems.append("no layers")
        layers = []
    sources = style.get("sources") or {}
    if (sources.get("openmaptiles") or {}).get("url") != PLANET:
        problems.append("openmaptiles source is not the OpenFreeMap planet")
    for key, source in sources.items():
        # The app's rule (isMapStyle in src/services/mapStyle/validate.ts): only the two kinds these
        # styles use and no other field that could point elsewhere, so nothing is published that every
        # phone would refuse (Codex task 1-3 review 3).
        if not isinstance(source, dict) or source.get("type") not in SOURCE_TYPES or "data" in source or "urls" in source:
            problems.append(f"source {key} is not a vector or raster source")
            continue
        urls = ([source["url"]] if "url" in source else []) + list(source.get("tiles") or [])
        if not urls or any(not (isinstance(u, str) and u.startswith(OFM)) for u in urls):
            problems.append(f"source {key} is not on tiles.openfreemap.org")
    for key in ("glyphs", "sprite"):
        if any(not (isinstance(u, str) and u.startswith(OFM)) for u in _urls(style.get(key))):
            problems.append(f"{key} is not on tiles.openfreemap.org")
    if changed <= 0:
        problems.append("no label layer was changed to Chinese")
    for layer in layers:
        if not (isinstance(layer, dict) and isinstance(layer.get("id"), str) and isinstance(layer.get("type"), str)):
            problems.append("a layer has no id or type")
            continue
        layout = layer.get("layout") or {}
        field = layout.get("text-field")
        # A plain text search on purpose, not transform.py's parser: a guard that shares the parser's
        # assumptions breaks together with it. So it also stops a label that only tests a latin field
        # (["has", "name:latin"]) or holds one as a literal. That false alarm holds back one monthly
        # publish (phones keep the style they have) until someone looks (Codex task 1-3 review 2).
        if field is not None and any(f in json.dumps(field, ensure_ascii=False) for f in LATIN_FIELDS):
            problems.append(f"layer {layer.get('id')} mentions a latin name field (check by hand if it only tests one)")
        font = layout.get("text-font")
        if font is not None and (not isinstance(font, list) or any(not str(f).startswith("Noto Sans") for f in font)):
            problems.append(f"layer {layer.get('id')} uses a font other than Noto Sans")
    meta = (style.get("metadata") or {}).get(ATTRIBUTION_KEY)
    problems += _attribution_problems(meta)
    # The independent gate: exactly what transform.py writes from mapstyle/notices (Codex plan review 10).
    if isinstance(meta, dict) and meta != attribution(name):
        problems.append("attribution differs from mapstyle/transform.py and mapstyle/notices")
    if len(encoded) > MAX_BYTES:
        problems.append(f"{len(encoded)} bytes, above {MAX_BYTES}")
    if problems:
        raise StyleCheckFailed(f"{name}: " + "; ".join(problems))
