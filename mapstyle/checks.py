"""Checks a map style must pass before it is published (D1 spec §2.1 item 3)."""
from __future__ import annotations

import json

from mapstyle.transform import ATTRIBUTION_KEY, attribution

OFM = "https://tiles.openfreemap.org/"
PLANET = "https://tiles.openfreemap.org/planet"
MAX_BYTES = 512 * 1024
LATIN_FIELDS = ("name:latin", "name_en", "name:en", "name_int", "name:nonlatin")
LAYER_FIELDS = ("work", "author", "url", "license", "licenseUrl")


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
        urls = ([source["url"]] if "url" in source else []) + list(source.get("tiles") or [])
        if not urls or any(not (isinstance(u, str) and u.startswith(OFM)) for u in urls):
            problems.append(f"source {key} is not on tiles.openfreemap.org")
    for key in ("glyphs", "sprite"):
        if any(not (isinstance(u, str) and u.startswith(OFM)) for u in _urls(style.get(key))):
            problems.append(f"{key} is not on tiles.openfreemap.org")
    if changed <= 0:
        problems.append("no label layer was changed to Chinese")
    for layer in layers:
        layout = layer.get("layout") or {}
        field = layout.get("text-field")
        if field is not None and any(f in json.dumps(field, ensure_ascii=False) for f in LATIN_FIELDS):
            problems.append(f"layer {layer.get('id')} still shows a latin name")
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
