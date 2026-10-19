import pytest

osmium = pytest.importorskip("osmium")

from search.nearby import classify, extract_nearby, pbf_timestamp

OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
 <node id="1" lat="22.5950" lon="120.3070" version="1"><tag k="amenity" v="motorcycle_parking"/></node>
 <node id="2" lat="22.5960" lon="120.3080" version="1"><tag k="amenity" v="parking"/><tag k="access" v="private"/></node>
 <node id="3" lat="22.5970" lon="120.3090" version="1"><tag k="railway" v="tram_stop"/><tag k="name" v="夢時代站"/></node>
 <node id="10" lat="22.6000" lon="120.3000" version="1"/>
 <node id="11" lat="22.6000" lon="120.3010" version="1"/>
 <node id="12" lat="22.6010" lon="120.3010" version="1"/>
 <node id="13" lat="22.6010" lon="120.3000" version="1"/>
 <node id="20" lat="22.6100" lon="120.3100" version="1"><tag k="public_transport" v="station"/><tag k="subway" v="yes"/><tag k="name" v=" 捷運站 "/></node>
 <node id="21" lat="22.6200" lon="120.3200" version="1"><tag k="public_transport" v="station"/><tag k="bus" v="yes"/></node>
 <node id="30" lat="22.7000" lon="120.4000" version="1"/>
 <node id="31" lat="22.7000" lon="120.4020" version="1"/>
 <node id="32" lat="22.7020" lon="120.4020" version="1"/>
 <node id="33" lat="22.7020" lon="120.4000" version="1"/>
 <way id="100" version="1"><nd ref="10"/><nd ref="11"/><nd ref="12"/><nd ref="13"/><nd ref="10"/><tag k="amenity" v="parking"/><tag k="name" v="夢時代停車場"/></way>
 <way id="101" version="1"><nd ref="30"/><nd ref="31"/><nd ref="32"/><nd ref="33"/><nd ref="30"/></way>
 <relation id="500" version="1"><member type="way" ref="101" role="outer"/><tag k="type" v="multipolygon"/><tag k="amenity" v="parking"/><tag k="name" v="大停車場"/></relation>
</osm>
"""


def test_extract_nearby_collects_points_ways_and_multipolygons(tmp_path):
    path = tmp_path / "sample.osm"
    path.write_text(OSM_XML, encoding="utf-8")
    items, stats = extract_nearby(path)
    by_id = {i["id"]: i for i in items}
    assert len(items) == len(by_id)  # A closed way must not also be emitted by area().
    assert set(by_id) == {"N1", "N3", "N20", "W100", "R500"}
    assert by_id["N1"] == {"id": "N1", "kind": "motorcycle_parking", "name": None,
                           "_geo": {"lat": 22.595, "lng": 120.307}}
    assert (by_id["N3"]["kind"], by_id["N3"]["name"]) == ("station", "夢時代站")
    assert by_id["N20"]["name"] == "捷運站"
    assert by_id["W100"]["_geo"] == pytest.approx(
        {"lat": 22.6005, "lng": 120.3005}, rel=0, abs=1e-7,
    )
    assert (by_id["R500"]["kind"], by_id["R500"]["name"]) == ("parking", "大停車場")
    assert by_id["R500"]["_geo"] == pytest.approx(
        {"lat": 22.701, "lng": 120.401}, rel=0, abs=1e-7,
    )
    assert stats.by_kind == {"motorcycle_parking": 1, "station": 2, "parking": 2}
    assert (stats.nodes, stats.ways, stats.relations, stats.documents) == (3, 1, 1, 5)


def test_pbf_timestamp_is_none_without_a_replication_header(tmp_path):
    path = tmp_path / "sample.osm"
    path.write_text(OSM_XML, encoding="utf-8")
    assert pbf_timestamp(path) is None


@pytest.mark.parametrize("tags,expected", [
    ({"amenity": "motorcycle_parking"}, "motorcycle_parking"),
    ({"amenity": "parking"}, "parking"),
    ({"amenity": "parking", "access": "no"}, None),
    ({"amenity": "parking", "motorcycle": "no"}, None),
    ({"railway": "station"}, "station"),
    ({"railway": "halt"}, "station"),
    ({"public_transport": "station", "light_rail": "yes"}, "station"),
    ({"public_transport": "station", "station": "subway"}, "station"),
    ({"public_transport": "station", "bus": "yes"}, None),
    ({"highway": "bus_stop"}, None),
    ({}, None),
    ({"amenity": "parking", "access": "private"}, None),
    ({"amenity": "parking", "access": "customers", "motorcycle": "yes"}, "parking"),
    # Section 3.4 applies the access exclusions to general parking only.
    ({"amenity": "motorcycle_parking", "access": "private"}, "motorcycle_parking"),
    ({"amenity": "motorcycle_parking", "access": "no"}, "motorcycle_parking"),
    ({"amenity": "motorcycle_parking", "motorcycle": "no"}, "motorcycle_parking"),
    ({"railway": "tram_stop"}, "station"),
    ({"public_transport": "station", "train": "yes"}, "station"),
    ({"public_transport": "station", "subway": "yes"}, "station"),
    ({"public_transport": "station", "station": "train"}, "station"),
    ({"public_transport": "station", "station": "light_rail"}, "station"),
    ({"public_transport": "station", "bus": "yes", "train": "yes"}, "station"),
    ({"public_transport": "station"}, None),
    ({"public_transport": "station", "station": "bus"}, None),
    ({"public_transport": "station", "train": "no"}, None),
    ({"public_transport": "platform", "train": "yes"}, None),
    ({"public_transport": "stop_position", "subway": "yes"}, None),
    ({"railway": "platform"}, None),
    ({"disused:railway": "station"}, None),
    ({"station": "subway"}, None),
])
def test_classify(tags, expected):
    assert classify(tags) == expected


def _extract_xml(tmp_path, body):
    path = tmp_path / "sample.osm"
    path.write_text(f'<osm version="0.6" generator="test">{body}</osm>', encoding="utf-8")
    return extract_nearby(path)


def test_open_way_is_collected_with_blank_name_and_rounded_coordinates(tmp_path):
    items, stats = _extract_xml(tmp_path, """
      <node id="1" lat="22.6000000" lon="120.3000000" version="1"/>
      <node id="2" lat="22.6000000" lon="120.3000000" version="1"/>
      <node id="3" lat="22.6010000" lon="120.3020000" version="1"/>
      <way id="10" version="1">
        <nd ref="1"/><nd ref="2"/><nd ref="3"/>
        <tag k="amenity" v="motorcycle_parking"/><tag k="name" v="   "/>
      </way>
    """)
    assert items == [{
        "id": "W10", "kind": "motorcycle_parking", "name": None,
        "_geo": {"lat": 22.6003333, "lng": 120.3006667},
    }]
    assert (stats.nodes, stats.ways, stats.relations, stats.documents) == (0, 1, 0, 1)
    assert stats.by_kind == {"motorcycle_parking": 1}


@pytest.mark.parametrize("key,value", [
    ("access", "private"), ("access", "no"), ("motorcycle", "no"),
])
def test_parking_exclusions_apply_to_ways_and_relations(tmp_path, key, value):
    path = tmp_path / "excluded.osm"
    # The existing private node is already excluded; change both parking areas.
    xml = OSM_XML.replace(
        '<tag k="amenity" v="parking"/><tag k="name"',
        f'<tag k="amenity" v="parking"/><tag k="{key}" v="{value}"/><tag k="name"',
    )
    path.write_text(xml, encoding="utf-8")
    items, stats = extract_nearby(path)
    assert {item["id"] for item in items} == {"N1", "N3", "N20"}
    assert (stats.nodes, stats.ways, stats.relations, stats.documents) == (3, 0, 0, 3)
    assert stats.by_kind == {"motorcycle_parking": 1, "station": 2}


@pytest.mark.parametrize("tags,kind", [
    ('<tag k="amenity" v="parking"/>', "parking"),
    ('<tag k="public_transport" v="station"/><tag k="subway" v="yes"/>', "station"),
])
def test_multipolygon_joins_untagged_ways_and_ignores_inner_ring(tmp_path, tags, kind):
    items, stats = _extract_xml(tmp_path, f"""
      <node id="1" lat="22.600" lon="120.300" version="1"/>
      <node id="2" lat="22.600" lon="120.308" version="1"/>
      <node id="3" lat="22.608" lon="120.308" version="1"/>
      <node id="4" lat="22.608" lon="120.300" version="1"/>
      <node id="5" lat="22.601" lon="120.301" version="1"/>
      <node id="6" lat="22.601" lon="120.302" version="1"/>
      <node id="7" lat="22.602" lon="120.302" version="1"/>
      <node id="8" lat="22.602" lon="120.301" version="1"/>
      <way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/></way>
      <way id="11" version="1"><nd ref="1"/><nd ref="4"/><nd ref="3"/></way>
      <way id="12" version="1"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/></way>
      <relation id="500" version="1">
        <member type="way" ref="11" role="outer"/>
        <member type="way" ref="12" role="inner"/>
        <member type="way" ref="10" role="outer"/>
        <tag k="type" v="multipolygon"/>{tags}
      </relation>
    """)
    # Member ways must not inherit relation tags and become extra candidates.
    assert items == [{
        "id": "R500", "kind": kind, "name": None,
        "_geo": {"lat": 22.604, "lng": 120.304},
    }]
    assert (stats.nodes, stats.ways, stats.relations, stats.documents) == (0, 0, 1, 1)
    assert stats.by_kind == {kind: 1}


def test_multipolygon_does_not_double_count_each_outer_rings_closing_node(tmp_path):
    items, stats = _extract_xml(tmp_path, """
      <node id="1" lat="22.000" lon="120.000" version="1"/>
      <node id="2" lat="22.000" lon="120.004" version="1"/>
      <node id="3" lat="22.003" lon="120.000" version="1"/>
      <node id="4" lat="22.010" lon="120.010" version="1"/>
      <node id="5" lat="22.010" lon="120.013" version="1"/>
      <node id="6" lat="22.012" lon="120.010" version="1"/>
      <way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="1"/></way>
      <way id="11" version="1"><nd ref="4"/><nd ref="5"/><nd ref="6"/><nd ref="4"/></way>
      <relation id="500" version="1">
        <member type="way" ref="10" role="outer"/>
        <member type="way" ref="11" role="outer"/>
        <tag k="type" v="multipolygon"/><tag k="amenity" v="parking"/>
      </relation>
    """)
    assert len(items) == 1
    assert items[0]["id"] == "R500"
    # Task 2's vertex average: six vertices, excluding BOTH closing duplicates.
    # Unequal triangles make this independent of ring order/start chosen by osmium.
    assert items[0]["_geo"] == pytest.approx(
        {"lat": 22.0058333, "lng": 120.0061667}, rel=0, abs=1e-7,
    )
    assert (stats.relations, stats.documents) == (1, 1)


def test_same_station_keeps_distinct_osm_objects_for_server_deduplication(tmp_path):
    items, stats = _extract_xml(tmp_path, """
      <node id="1" lat="22.600" lon="120.300" version="1"/>
      <node id="2" lat="22.600" lon="120.302" version="1"/>
      <node id="3" lat="22.602" lon="120.302" version="1"/>
      <node id="4" lat="22.602" lon="120.300" version="1"/>
      <node id="100" lat="22.601" lon="120.301" version="1">
        <tag k="railway" v="station"/><tag k="name" v="中央站"/>
      </node>
      <way id="100" version="1">
        <nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
        <tag k="public_transport" v="station"/><tag k="train" v="yes"/>
        <tag k="name" v=" 中央站 "/>
      </way>
      <relation id="100" version="1">
        <member type="way" ref="100" role="outer"/>
        <tag k="type" v="multipolygon"/><tag k="railway" v="station"/>
        <tag k="name" v="中央站"/>
      </relation>
    """)
    assert len(items) == 3
    assert {item["id"] for item in items} == {"N100", "W100", "R100"}
    assert all(item["name"] == "中央站" and item["kind"] == "station" for item in items)
    assert all(item["_geo"] == {"lat": 22.601, "lng": 120.301} for item in items)
    assert (stats.nodes, stats.ways, stats.relations, stats.documents) == (1, 1, 1, 3)
    assert stats.by_kind == {"station": 3}


def test_empty_candidate_way_is_skipped_and_counted(tmp_path):
    items, stats = _extract_xml(tmp_path, """
      <node id="1" lat="22.600" lon="120.300" version="1">
        <tag k="highway" v="bus_stop"/>
      </node>
      <way id="10" version="1"><tag k="amenity" v="parking"/></way>
    """)
    assert items == []
    assert stats.as_dict() == {
        "nodes": 0, "ways": 0, "relations": 0, "without_locations": 1,
        "documents": 0, "by_kind": {},
    }


@pytest.mark.parametrize("timestamp", [None, "2026-10-18T20:21:22Z"])
def test_pbf_timestamp_reads_real_pbf_header(tmp_path, timestamp):
    path = tmp_path / "header.osm.pbf"
    header = osmium.io.Header()
    if timestamp is not None:
        header.set("osmosis_replication_timestamp", timestamp)
    header.set("osmosis_replication_sequence_number", "12345")
    header.set("osmosis_replication_base_url", "https://example.invalid/taiwan-updates")
    with osmium.SimpleWriter(str(path), header=header):
        pass
    assert pbf_timestamp(path) == timestamp
