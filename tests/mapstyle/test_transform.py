import copy
import json
import re
from pathlib import Path

import pytest

from mapstyle.transform import (CHINESE_NAME, NOTICE_DIR, STYLES, UnreadableLabel, attribution, chinese_labels, encode,
                                transform)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / f"style-{name}.json").read_text(encoding="utf-8"))


# Not the module's parser: a plain look for a name token or ["get", "name…"] in the JSON.
NAME_REFERENCE = re.compile(r'\{name|"get",\s*"name')


@pytest.mark.parametrize("name", ["liberty", "fiord"])
def test_every_label_that_reads_a_name_shows_chinese_and_nothing_else_changes(name):
    before = load(name)
    after, changed = chinese_labels(before)
    assert changed > 0
    assert len(after["layers"]) == len(before["layers"])
    for old, new in zip(before["layers"], after["layers"]):
        field = (old.get("layout") or {}).get("text-field")
        if (new.get("layout") or {}).get("text-field") == CHINESE_NAME and field != CHINESE_NAME:
            assert NAME_REFERENCE.search(json.dumps(field, ensure_ascii=False)), old["id"]
            assert {k: v for k, v in new["layout"].items() if k != "text-field"} == {
                k: v for k, v in old["layout"].items() if k != "text-field"}
            assert {k: v for k, v in new.items() if k != "layout"} == {k: v for k, v in old.items() if k != "layout"}
        else:
            assert new == old
            if field is not None:
                assert not NAME_REFERENCE.search(json.dumps(field, ensure_ascii=False)), f"{old['id']} still shows a name"
    for key in ("version", "sources", "glyphs", "sprite"):
        assert after[key] == before[key]


def test_the_input_is_not_changed():
    before = load("liberty")
    snapshot = copy.deepcopy(before)
    chinese_labels(before)
    transform("liberty", before)
    assert before == snapshot


@pytest.mark.parametrize("field", [
    "{name:latin}\n{name:nonlatin}",
    "{name}",
    ["get", "name_en"],
    ["coalesce", ["get", "name:latin"], ["get", "name"]],
    ["concat", ["get", "name:latin"], " ", ["get", "name:nonlatin"]],
    ["format", ["get", "name"], {"font-scale": 0.8}],
    ["case", ["has", "name"], ["get", "name"], ["get", "ref"]],
    ["step", ["zoom"], ["get", "ref"], 10, ["get", "name"]],
    ["match", ["get", "class"], "motorway", ["get", "ref"], ["get", "name"]],
])
def test_any_way_of_reading_a_name_is_replaced(field):
    style = {"version": 8, "layers": [{"id": "l", "type": "symbol", "layout": {"text-field": field}}]}
    out, changed = chinese_labels(style)
    assert changed == 1
    assert out["layers"][0]["layout"]["text-field"] == CHINESE_NAME


@pytest.mark.parametrize("field", [
    "{ref}",
    ["get", "ref"],
    ["to-string", ["get", "housenumber"]],
    ["concat", "name: ", ["get", "ref"]],
    "name",
    ["get", "names"],
    ["literal", "{name}"],
    ["match", ["get", "network"], "name", ["get", "ref"], ""],
    ["case", ["has", "name"], ["get", "ref"], ""],
])
def test_labels_that_do_not_read_a_name_stay(field):
    style = {"version": 8, "layers": [{"id": "l", "type": "symbol", "layout": {"text-field": field}},
                                      {"id": "f", "type": "fill", "paint": {}}]}
    out, changed = chinese_labels(style)
    assert changed == 0
    assert out == style


@pytest.mark.parametrize("field", [
    ["get", ["concat", "na", "me"]],
    ["get", "name", ["properties"]],
    {"property": "name", "stops": [[0, "a"]]},
    ["number-format", ["get", "name"], {}],
])
def test_a_label_this_parser_does_not_understand_stops_the_build(field):
    style = {"version": 8, "layers": [{"id": "l", "type": "symbol", "layout": {"text-field": field}}]}
    with pytest.raises(UnreadableLabel):
        chinese_labels(style)


@pytest.mark.parametrize("name", ["liberty", "fiord"])
def test_attribution_carries_the_credit_every_layer_and_the_notices_verbatim(name):
    a = attribution(name)
    assert a["version"] == 1 and a["style"] == name and a["title"]
    assert {c["url"] for c in a["credit"]} == {"https://openmaptiles.org/", "https://www.openstreetmap.org/copyright"}
    assert len(a["layers"]) == 3
    for layer in a["layers"]:
        for key in ("work", "author", "url", "license", "licenseUrl"):
            assert isinstance(layer[key], str) and layer[key]
    assert [n["file"] for n in a["notices"]] == STYLES[name]["notices"]
    for notice in a["notices"]:
        assert notice["text"] == (NOTICE_DIR / notice["file"]).read_text(encoding="utf-8")
    assert any("中文" in m for m in a["modifications"])


def test_fiord_credits_cartodb_basemaps_under_cc_by_3():
    last = attribution("fiord")["layers"][-1]
    assert last["url"] == "https://github.com/CartoDB/CartoDB-basemaps"
    assert last["licenseUrl"] == "https://creativecommons.org/licenses/by/3.0/"


def test_liberty_credits_mapbox_open_styles_under_cc_by_3():
    last = attribution("liberty")["layers"][-1]
    assert last["url"] == "https://github.com/mapbox/mapbox-gl-styles"
    assert last["licenseUrl"] == "https://creativecommons.org/licenses/by/3.0/"


@pytest.mark.parametrize("name", ["liberty", "fiord"])
def test_transform_adds_the_attribution_and_keeps_other_metadata(name):
    upstream = load(name)
    upstream["metadata"] = {"other": 1}
    out, changed = transform(name, upstream)
    assert changed > 0
    assert out["metadata"]["other"] == 1
    assert out["metadata"]["stormrider:attribution"] == attribution(name)


def test_encode_is_stable_utf8_json():
    style, _ = transform("liberty", load("liberty"))
    body = encode(style)
    assert body == encode(json.loads(body))
    assert "地名" in body.decode("utf-8")
