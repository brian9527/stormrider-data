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


@pytest.mark.parametrize("layer", [{"type": "background"}, {"id": "x"}, {"id": 1, "type": "fill"}, "layer",
                                   {"id": "  ", "type": "background"}, {"id": "x", "type": " "}])
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


def layer(style, layer_id):
    return next(l for l in style["layers"] if l["id"] == layer_id)


def every_openmaptiles_layer_elsewhere(style):
    style["sources"]["other"] = {"type": "vector", "url": "https://tiles.openfreemap.org/planet"}
    for l in style["layers"]:
        if l.get("source") == "openmaptiles":
            l["source"] = "other"


# The app refuses all of these (src/services/mapStyle/validate.ts), so publishing one would leave every
# phone on its old style while the workflow says it succeeded (Codex final review, data 1 and 3).
@pytest.mark.parametrize("break_it, message", [
    (every_openmaptiles_layer_elsewhere, "no layer draws openmaptiles"),
    (lambda s: s["sources"]["openmaptiles"].update(type="raster"), "openmaptiles is not vector tiles"),
    (lambda s: s["layers"].append(dict(s["layers"][-1])), "used twice"),
    (lambda s: s["layers"][-1].update(source="gone"), "source gone does not exist"),
    (lambda s: s["layers"][-1].update(source="constructor"), "source constructor does not exist"),
    (lambda s: s["layers"][-1].pop("source"), "names no source"),
    (lambda s: layer(s, "background").update(source="openmaptiles"), "background .* names a source"),
    (lambda s: layer(s, "background").update(source=None), "background .* names a source"),
    (lambda s: (s["sources"].update({" ": dict(s["sources"]["openmaptiles"])}), s["layers"][-1].update(source=" ")),
     "names no source"),
    (lambda s: s["sources"].update(extra={"type": "raster", "tiles": {"https://tiles.openfreemap.org/x/{z}/{x}/{y}.png": 1}}),
     "source extra"),
    (lambda s: s["sources"]["ne2_shaded"].update(tiles={"https://tiles.openfreemap.org/x/{z}/{x}/{y}.png": 1}), "source ne2_shaded"),
    (lambda s: s["sources"]["ne2_shaded"].update(tiles=[], url="https://tiles.openfreemap.org/x"), "source ne2_shaded"),
    (lambda s: s["layers"][-1].update(type="sparkle"), "type sparkle"),
    (lambda s: s.update(glyphs=["https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf"]), "glyphs"),
    (lambda s: s.update(glyphs=[]), "glyphs"),
    (lambda s: s.update(glyphs="https://tiles.openfreemap.org/fonts/{range}.pbf"), "glyphs"),
    (lambda s: s.update(glyphs="https://tiles.openfreemap.org/fonts/{fontstack}.pbf"), "glyphs"),
    (lambda s: s.update(sprite=[]), "sprite"),
    (lambda s: s.update(sprite=[{"url": "https://tiles.openfreemap.org/sprites/ofm_f384/ofm"}]), "sprite"),
    (lambda s: s.update(sprite=["https://tiles.openfreemap.org/sprites/ofm_f384/ofm"]), "sprite"),
])
def test_nothing_is_published_that_the_app_would_refuse(break_it, message):
    style, changed = valid()
    break_it(style)
    with pytest.raises(StyleCheckFailed, match=message):
        check_style("liberty", style, changed, encode(style))


def test_a_sprite_list_with_ids_passes():
    style, changed = valid()
    style["sprite"] = [{"id": "default", "url": "https://tiles.openfreemap.org/sprites/ofm_f384/ofm"}]
    check_style("liberty", style, changed, encode(style))


# Written out here, not imported: the test must not follow a change of transform.py's own constant.
EXPECTED_LABEL = ["coalesce", ["get", "name:zh-Hant"], ["get", "name"]]
KEY_LABELS = {
    "liberty": ["label_country_1", "label_state", "label_city_capital", "label_city", "label_town", "label_village",
                "highway-name-major", "water_name_point_label"],
    "fiord": ["place_country_major", "place_state", "place_city_large", "place_city", "place_town", "place_village",
              "highway_name_other", "water_name"],
}


@pytest.mark.parametrize("name, layer_id", [(n, i) for n, ids in KEY_LABELS.items() for i in ids])
def test_every_key_label_layer_must_be_there_and_show_chinese(name, layer_id):
    style, changed = valid(name)
    assert layer(style, layer_id)["layout"]["text-field"] == EXPECTED_LABEL
    # An upstream field the parser takes for something else than a name: kept as it is, unseen by the
    # latin-name search, while other layers still count as changed (Codex final review, data 4).
    layer(style, layer_id)["layout"]["text-field"] = ["get", "label_en"]
    with pytest.raises(StyleCheckFailed, match=f"label {layer_id} does not show the Chinese name"):
        check_style(name, style, changed, encode(style))
    style["layers"] = [l for l in style["layers"] if l["id"] != layer_id]
    with pytest.raises(StyleCheckFailed, match=f"label {layer_id} does not show the Chinese name"):
        check_style(name, style, changed, encode(style))


def test_another_style_under_this_name_fails():
    # Fiord's address answering with Liberty: transform() would put Fiord's credits on it (data 2).
    upstream = json.loads((FIXTURES / "style-liberty.json").read_text(encoding="utf-8"))
    style, changed = transform("fiord", upstream)
    with pytest.raises(StyleCheckFailed, match="does not show the Chinese name"):
        check_style("fiord", style, changed, encode(style))


@pytest.mark.parametrize("break_it", [
    lambda l: l["layout"].pop("text-font"),
    lambda l: l["layout"].update({"text-font": []}),
    lambda l: l["layout"].update({"text-font": "Noto Sans Regular"}),
    lambda l: l["layout"].update({"text-font": ["Noto SansRegular"]}),
    lambda l: l["layout"].update({"text-font": ["Noto Sans Regular", "Arial Unicode MS Regular"]}),
])
def test_every_text_layer_names_a_noto_font(break_it):
    # Without text-font MapLibre falls back to fonts outside the licence record (data 5).
    style, changed = valid()
    break_it(layer(style, "label_city"))
    with pytest.raises(StyleCheckFailed, match="label_city uses a font other than Noto Sans"):
        check_style("liberty", style, changed, encode(style))


def test_a_font_inside_a_format_expression_fails():
    style, changed = valid()
    layer(style, "highway-shield-non-us")["layout"]["text-field"] = [
        "format", ["get", "ref"], {"text-font": ["literal", ["Arial Unicode MS Regular"]]}]
    with pytest.raises(StyleCheckFailed, match="highway-shield-non-us sets a font inside its text"):
        check_style("liberty", style, changed, encode(style))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_numbers_the_app_cannot_parse_are_not_encoded(value):
    style, _ = valid()
    style["center"] = [value, 0]
    with pytest.raises(ValueError):
        encode(style)
