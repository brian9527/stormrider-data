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
# Layer types both MapLibre Native versions in the app draw from those sources.
LAYER_TYPES = ("background", "fill", "line", "symbol", "circle", "heatmap", "fill-extrusion", "raster")
GLYPH_PARTS = ("{fontstack}", "{range}")
# The fonts the licence research covers (app repo, 2026-11-21-d1-map-licenses §0 item 3).
FONTS = ("Noto Sans Regular", "Noto Sans Bold", "Noto Sans Italic")
# Written out here, not imported from transform.py: a guard that shares the transform's constant would
# follow it when it breaks. "Some label changed" is not enough: an upstream field the parser does not
# take for a name leaves most labels as they were (Codex final review, data 4).
EXPECTED_LABEL = ["coalesce", ["get", "name:zh-Hant"], ["get", "name"]]
KEY_LABELS = {
    "liberty": ("label_country_1", "label_state", "label_city_capital", "label_city", "label_town", "label_village",
                "highway-name-major", "water_name_point_label"),
    "fiord": ("place_country_major", "place_state", "place_city_large", "place_city", "place_town", "place_village",
              "highway_name_other", "water_name"),
}


class StyleCheckFailed(Exception):
    pass


def _on_ofm(value) -> bool:
    return isinstance(value, str) and value.startswith(OFM)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sprite_ok(sprite) -> bool:
    """A URL, or a list of {id, url}."""
    if isinstance(sprite, list):
        return bool(sprite) and all(isinstance(s, dict) and _text(s.get("id")) and _on_ofm(s.get("url")) for s in sprite)
    return _on_ofm(sprite)


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
    openmaptiles = sources.get("openmaptiles") if isinstance(sources.get("openmaptiles"), dict) else {}
    if openmaptiles.get("url") != PLANET:
        problems.append("openmaptiles source is not the OpenFreeMap planet")
    if openmaptiles.get("type") != "vector":
        problems.append("openmaptiles is not vector tiles")
    for key, source in sources.items():
        # The app's rule (isMapStyle in src/services/mapStyle/validate.ts): only the two kinds these
        # styles use and no other field that could point elsewhere, so nothing is published that every
        # phone would refuse (Codex task 1-3 review 3).
        if not isinstance(source, dict) or source.get("type") not in SOURCE_TYPES or "data" in source or "urls" in source:
            problems.append(f"source {key} is not a vector or raster source")
            continue
        tiles = source.get("tiles")
        if "tiles" in source and not (isinstance(tiles, list) and tiles):
            problems.append(f"source {key} lists no tile addresses")
        urls = ([source["url"]] if "url" in source else []) + (tiles if isinstance(tiles, list) else [])
        if not urls or not all(_on_ofm(u) for u in urls):
            problems.append(f"source {key} is not on tiles.openfreemap.org")
    glyphs = style.get("glyphs")
    if not _on_ofm(glyphs) or any(part not in glyphs for part in GLYPH_PARTS):
        problems.append("glyphs is not a font address on tiles.openfreemap.org")
    if not _sprite_ok(style.get("sprite")):
        problems.append("sprite is not on tiles.openfreemap.org")
    if changed <= 0:
        problems.append("no label layer was changed to Chinese")
    # The rest of the loop holds back what the app's isMapStyle (src/services/mapStyle/validate.ts) would
    # refuse, so a publish never leaves every phone on its old style; keep the two in step.
    seen = {}
    for layer in layers:
        if not (isinstance(layer, dict) and _text(layer.get("id")) and _text(layer.get("type"))):
            problems.append("a layer has no id or type")
            continue
        layer_id, kind, source = layer["id"], layer["type"], layer.get("source")
        if layer_id in seen:
            problems.append(f"layer id {layer_id} is used twice")
        seen[layer_id] = layer
        if kind not in LAYER_TYPES:
            problems.append(f"layer {layer_id} has type {kind}")
        if kind == "background":
            if "source" in layer:
                problems.append(f"background layer {layer_id} names a source")
        elif not _text(source):
            problems.append(f"layer {layer_id} names no source")
        elif source not in sources:
            problems.append(f"layer {layer_id}: source {source} does not exist")
        layout = layer.get("layout") or {}
        field = layout.get("text-field")
        # A plain text search on purpose, not transform.py's parser: a guard that shares the parser's
        # assumptions breaks together with it. So it also stops a label that only tests a latin field
        # (["has", "name:latin"]) or holds one as a literal. That false alarm holds back one monthly
        # publish (phones keep the style they have) until someone looks (Codex task 1-3 review 2).
        if field is not None and any(f in json.dumps(field, ensure_ascii=False) for f in LATIN_FIELDS):
            problems.append(f"layer {layer_id} mentions a latin name field (check by hand if it only tests one)")
        # Without text-font MapLibre uses its own default fonts, which the licence research does not
        # cover; a format expression can name fonts of its own (Codex final review, data 5).
        font = layout.get("text-font")
        if (field is not None or font is not None) and (
            not isinstance(font, list) or not font or any(f not in FONTS for f in font)
        ):
            problems.append(f"layer {layer_id} uses a font other than Noto Sans")
        if field is not None and '"text-font"' in json.dumps(field, ensure_ascii=False):
            problems.append(f"layer {layer_id} sets a font inside its text")
    if not any(layer.get("source") == "openmaptiles" for layer in seen.values()):
        problems.append("no layer draws openmaptiles")
    for key in KEY_LABELS[name]:
        if ((seen.get(key) or {}).get("layout") or {}).get("text-field") != EXPECTED_LABEL:
            problems.append(f"label {key} does not show the Chinese name")
    meta = (style.get("metadata") or {}).get(ATTRIBUTION_KEY)
    problems += _attribution_problems(meta)
    # The independent gate: exactly what transform.py writes from mapstyle/notices (Codex plan review 10).
    if isinstance(meta, dict) and meta != attribution(name):
        problems.append("attribution differs from mapstyle/transform.py and mapstyle/notices")
    if len(encoded) > MAX_BYTES:
        problems.append(f"{len(encoded)} bytes, above {MAX_BYTES}")
    if problems:
        raise StyleCheckFailed(f"{name}: " + "; ".join(problems))
