import json
from pathlib import Path

import pytest

from mapstyle.checks import MAX_BYTES, StyleCheckFailed, check_style
from mapstyle.transform import encode, reads_name, transform

FIXTURES = Path(__file__).parent / "fixtures"


def valid(name="liberty"):
    upstream = json.loads((FIXTURES / f"style-{name}.json").read_text(encoding="utf-8"))
    return transform(name, upstream)


@pytest.mark.parametrize("name", ["liberty", "fiord"])
def test_a_transformed_style_passes(name):
    style, changed = valid(name)
    check_style(name, style, changed, encode(style))


@pytest.mark.parametrize("break_it, message", [
    (lambda s: s.update(version=7), "version"),
    (lambda s: s.update(layers=[]), "no layers"),
    (lambda s: s["sources"]["openmaptiles"].update(url="https://example.com/planet"), "openmaptiles"),
    (lambda s: s["sources"]["ne2_shaded"].update(tiles=["https://example.com/{z}/{x}/{y}.png"]), "ne2_shaded"),
    (lambda s: s.update(glyphs="https://example.com/fonts/{fontstack}/{range}.pbf"), "glyphs"),
    (lambda s: s.update(sprite="https://example.com/sprite"), "sprite"),
    (lambda s: s["layers"].append({"id": "x", "type": "symbol", "layout": {"text-field": "{name:latin}"}}), "latin"),
    (lambda s: s["layers"].append({"id": "y", "type": "symbol",
                                   "layout": {"text-field": "{ref}", "text-font": ["Arial Unicode MS Regular"]}}), "font"),
    (lambda s: s["metadata"].pop("stormrider:attribution"), "attribution"),
    (lambda s: s["metadata"]["stormrider:attribution"]["layers"][0].update(url=""), "attribution"),
    (lambda s: s["metadata"]["stormrider:attribution"].update(notices=[]), "attribution"),
    (lambda s: s["metadata"]["stormrider:attribution"]["notices"][0].update(text="x"), "attribution"),
    (lambda s: s["metadata"]["stormrider:attribution"]["notices"].pop(), "attribution"),
    (lambda s: s["metadata"]["stormrider:attribution"].update(modifications=[]), "attribution"),
    (lambda s: s["metadata"]["stormrider:attribution"]["credit"][0].update(url="https://example.com/"), "attribution"),
])
def test_each_check_fails_on_its_own_problem(break_it, message):
    style, changed = valid()
    break_it(style)
    with pytest.raises(StyleCheckFailed, match=message):
        check_style("liberty", style, changed, encode(style))


@pytest.mark.parametrize("source", [
    {"type": "image", "url": "https://tiles.openfreemap.org/x.png", "coordinates": [[0, 1], [1, 1], [1, 0], [0, 0]]},
    {"type": "raster-dem", "url": "https://tiles.openfreemap.org/dem"},
    {"type": "geojson", "data": "https://tiles.openfreemap.org/x.geojson"},
    {"type": "vector", "url": "https://tiles.openfreemap.org/planet", "urls": ["https://example.com/x"]},
    {"type": "raster", "tiles": ["https://tiles.openfreemap.org/{z}/{x}/{y}.png"], "data": "https://example.com/x"},
    "https://tiles.openfreemap.org/planet",
])
def test_only_the_sources_the_app_accepts(source):
    style, changed = valid()
    style["sources"]["extra"] = source
    with pytest.raises(StyleCheckFailed, match="source extra is not a vector or raster source"):
        check_style("liberty", style, changed, encode(style))


@pytest.mark.parametrize("layer", [{"type": "background"}, {"id": "x"}, {"id": 1, "type": "fill"}, "layer"])
def test_every_layer_has_an_id_and_a_type(layer):
    style, changed = valid()
    style["layers"].append(layer)
    with pytest.raises(StyleCheckFailed, match="no id or type"):
        check_style("liberty", style, changed, encode(style))


def test_the_latin_check_is_a_plain_text_search_on_purpose():
    # Shows the road number and only tests name:latin: transform.py rightly keeps it, and the guard
    # still stops it, so the guard never relies on transform.py's parser (Codex task 1-3 review 2).
    field = ["case", ["has", "name:latin"], ["get", "ref"], ""]
    assert not reads_name(field)
    style, changed = valid()
    style["layers"].append({"id": "shield", "type": "symbol", "layout": {"text-field": field}})
    with pytest.raises(StyleCheckFailed, match="shield mentions a latin name field"):
        check_style("liberty", style, changed, encode(style))


def test_no_changed_label_fails():
    style, _ = valid()
    with pytest.raises(StyleCheckFailed, match="label"):
        check_style("liberty", style, 0, encode(style))


def test_an_oversized_file_fails():
    style, changed = valid()
    with pytest.raises(StyleCheckFailed, match="bytes"):
        check_style("liberty", style, changed, b"x" * (MAX_BYTES + 1))


def test_a_sprite_list_is_checked_entry_by_entry():
    style, changed = valid()
    style["sprite"] = [{"id": "default", "url": "https://tiles.openfreemap.org/sprites/ofm_f384/ofm"},
                       {"id": "other", "url": "https://example.com/sprite"}]
    with pytest.raises(StyleCheckFailed, match="sprite"):
        check_style("liberty", style, changed, encode(style))
