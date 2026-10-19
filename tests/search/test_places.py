import gzip
import hashlib
import io
import json

import pytest

zstandard = pytest.importorskip("zstandard")

from search.places import PlaceStats, build_documents, iter_dump_records, make_doc, write_ndjson_gz


def rec(**overrides):
    base = {
        "object_type": "N", "object_id": 1, "osm_key": "amenity", "osm_value": "cafe",
        "name": {"name": "咖啡店"}, "centroid": [121.5, 25.0],
        "address": {"county": "臺北市", "city": "", "suburb": "大安區"}, "importance": 0.1,
    }
    base.update(overrides)
    return base


def zst_bytes(raw_lines):
    return io.BytesIO(zstandard.ZstdCompressor().compress("".join(raw_lines).encode("utf-8")))


def test_make_doc_builds_the_m_fields():
    r = rec(
        name={"name": "統一夢時代購物中心", "name:en": "Dream Mall", "alt_name": "夢時代;統一夢時代購物中心"},
        extra={"short_name": "夢時代", "wikidata": "Q123"}, importance=0.4,
    )
    assert make_doc(r, 7) == {
        "doc_id": "N1-7", "key": "N1", "name": "統一夢時代購物中心",
        "aliases": ["Dream Mall", "夢時代"], "where": "臺北市 大安區",
        "osm_key": "amenity", "osm_value": "cafe", "wikidata": "Q123", "importance": 0.4,
        "_geo": {"lat": 25.0, "lng": 121.5},
    }


@pytest.mark.parametrize("bad", [
    {"object_type": None}, {"object_id": None}, {"name": {}}, {"name": {"name": "  "}},
    {"centroid": [121.5]}, {"centroid": "121.5,25"}, {"centroid": [121.5, None]},
    {"centroid": [float("nan"), 25.0]}, {"centroid": [121.5, float("inf")]},
    {"centroid": [121.5, 95.0]}, {"centroid": [190.0, 25.0]}, {"centroid": [True, 25.0]},
])
def test_make_doc_rejects_records_without_id_name_or_a_valid_centroid(bad):
    assert make_doc(rec(**bad), 0) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "0.5", None, True])
def test_make_doc_falls_back_to_zero_importance(value):
    assert make_doc(rec(importance=value), 0)["importance"] == 0.0


def test_write_ndjson_gz_refuses_non_finite_numbers(tmp_path):
    with pytest.raises(ValueError):
        write_ndjson_gz([{"x": float("nan")}], tmp_path / "bad.ndjson.gz")


def test_iter_dump_records_reads_place_lines_and_the_header():
    header_line = {"type": "NominatimDumpFile",
                   "content": {"version": "1.0", "data_timestamp": "2026-10-03T23:01:00Z", "features": {}}}
    lines = [
        json.dumps(header_line) + "\n",
        json.dumps({"type": "Place", "content": [rec(), "not a dict"]}, ensure_ascii=False) + "\n",
        "{broken\n",
        json.dumps({"type": "CountryInfo", "content": []}) + "\n",
    ]
    stats, header = PlaceStats(), {}
    assert list(iter_dump_records(zst_bytes(lines), stats, header)) == [rec()]
    assert stats.decode_errors == 2  # the broken line, and the content entry that is not an object
    assert stats.non_place_lines == 1
    assert header == {"type": "NominatimDumpFile", "content.version": "1.0",
                      "content.data_timestamp": "2026-10-03T23:01:00Z"}


def test_iter_dump_records_reads_concatenated_frames():
    # A dump may be written as several zstd frames back to back; all of them are data.
    first = dump_line(dump_header()) + dump_line({"type": "Place", "content": [rec()]})
    second = dump_line({"type": "Place", "content": [rec(object_id=2)]})
    c = zstandard.ZstdCompressor(write_checksum=True)
    stream = io.BytesIO(c.compress(first.encode("utf-8")) + c.compress(second.encode("utf-8")))
    stats = PlaceStats()
    assert list(iter_dump_records(stream, stats, {})) == [rec(), rec(object_id=2)]
    assert stats.decode_errors == 0


def test_iter_dump_records_rejects_a_frame_cut_off_mid_block():
    raw = "".join([dump_line(dump_header())] + [dump_line({"type": "Place", "content": [rec(object_id=i)]})
                                                 for i in range(200)])
    frame = zstandard.ZstdCompressor(write_checksum=True).compress(raw.encode("utf-8"))
    with pytest.raises(zstandard.ZstdError):
        list(iter_dump_records(io.BytesIO(frame[: len(frame) // 2]), PlaceStats(), {}))


def road(object_id, name, address, importance=0.1):
    return rec(object_id=object_id, osm_key="highway", osm_value="trunk", name=name,
               address=address, importance=importance)


def test_build_documents_merges_same_name_roads_per_district():
    records = [
        road(1, {"name": "南迴公路"}, {"county": "臺東縣", "city": "大武鄉"}, 0.2),
        road(2, {"name": "南迴公路", "alt_name": "台9線"}, {"county": "臺東縣", "city": "大武鄉"}, 0.5),
        road(3, {"name": "南迴公路"}, {"county": "屏東縣", "city": "枋山鄉"}, 0.3),
        road(4, {"name": "無名路"}, {}),
        road(5, {"name": "無名路"}, {}),
        rec(object_id=6),
    ]
    stats = PlaceStats()
    docs = list(build_documents(records, stats))
    assert sorted(d["key"] for d in docs) == ["N2", "N3", "N4", "N5", "N6"]
    assert next(d for d in docs if d["key"] == "N2")["aliases"] == ["台9線"]
    assert (stats.records, stats.roads_merged_away, stats.documents) == (6, 1, 5)


def test_build_documents_drops_parcel_lockers():
    records = [rec(object_id=1, osm_value="parcel_locker", name={"name": "i郵箱"}), rec(object_id=2)]
    stats = PlaceStats()
    assert [d["key"] for d in build_documents(records, stats)] == ["N2"]
    assert stats.excluded_category == 1


def test_build_documents_can_turn_both_additions_off_for_evaluation():
    records = [
        road(1, {"name": "南迴公路"}, {"county": "臺東縣", "city": "大武鄉"}),
        road(2, {"name": "南迴公路"}, {"county": "臺東縣", "city": "大武鄉"}),
        rec(object_id=3, osm_value="parcel_locker", name={"name": "i郵箱"}),
    ]
    docs = list(build_documents(records, PlaceStats(), merge_roads=False, exclude=False))
    assert sorted(d["key"] for d in docs) == ["N1", "N2", "N3"]


def test_write_ndjson_gz_round_trips(tmp_path):
    path = tmp_path / "p.ndjson.gz"
    count, sha = write_ndjson_gz([{"a": "夢"}, {"b": 1}], path)
    assert count == 2 and len(sha) == 64
    with gzip.open(path, "rt", encoding="utf-8") as f:
        assert [json.loads(line) for line in f] == [{"a": "夢"}, {"b": 1}]
    assert sha == hashlib.sha256(path.read_bytes()).hexdigest()


def dump_header():
    return {"type": "NominatimDumpFile", "content": {
        "version": "0.1.0", "data_timestamp": "2026-10-03T23:01:00Z",
    }}


def dump_line(value):
    return json.dumps(value, ensure_ascii=False) + "\n"


def skippable_frame(payload=b"metadata"):
    # Zstd skippable frame: little-endian magic, payload length, opaque bytes.
    return b"\x50\x2a\x4d\x18" + len(payload).to_bytes(4, "little") + payload


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_iter_dump_records_handles_frame_ends_at_the_source_chunk_boundary(offset):
    c = zstandard.ZstdCompressor(write_checksum=True)
    first = c.compress(dump_line(dump_header()).encode("utf-8"))
    second = c.compress(dump_line({"type": "Place", "content": [rec()]}).encode("utf-8"))
    # Padding makes the first data frame end just before, exactly at, or just
    # after the default 64 KiB read boundary, without relying on compressor sizes.
    prefix = skippable_frame(b"x" * ((1 << 16) + offset - len(first) - 8))
    assert len(prefix + first) == (1 << 16) + offset
    stats, header = PlaceStats(), {}
    assert list(iter_dump_records(io.BytesIO(prefix + first + second), stats, header)) == [rec()]
    assert stats.decode_errors == 0
    assert header["type"] == "NominatimDumpFile"


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("read_limit", [1, 1 << 16])
def test_iter_dump_records_ignores_complete_skippable_and_empty_frames(position, read_limit):
    class ShortReads(io.BytesIO):
        def read(self, size=-1):
            assert size > 0
            return super().read(min(size, read_limit))

    c = zstandard.ZstdCompressor(write_checksum=True)
    frames = [c.compress(dump_line(dump_header()).encode("utf-8")),
              c.compress(dump_line({"type": "Place", "content": [rec()]}).encode("utf-8"))]
    frames.insert(position, skippable_frame() + c.compress(b"") + skippable_frame(b""))
    stats = PlaceStats()
    assert list(iter_dump_records(ShortReads(b"".join(frames)), stats, {})) == [rec()]
    assert stats.decode_errors == 0


@pytest.mark.parametrize("tail", [
    b"\x28", b"\x28\xb5\x2f\xfd",  # a new ordinary frame's incomplete header
    skippable_frame()[:3], skippable_frame()[:7], skippable_frame()[:-1],
])
def test_iter_dump_records_rejects_incomplete_frames_after_a_complete_dump(tail):
    raw = (dump_line(dump_header()) + dump_line({"type": "Place", "content": [rec()]})).encode("utf-8")
    frame = zstandard.ZstdCompressor(write_checksum=True).compress(raw)
    with pytest.raises(zstandard.ZstdError):
        list(iter_dump_records(io.BytesIO(frame + tail), PlaceStats(), {}))


def assert_invalid_dump_is_reported(stream):
    """Either abort or set the counter that Task 3 checks before publishing."""
    stats = PlaceStats()
    try:
        list(iter_dump_records(stream, stats, {}))
    except (ValueError, zstandard.ZstdError):
        return
    assert stats.decode_errors > 0, "Invalid input was silently accepted"


@pytest.mark.parametrize("kind", ["no-bytes", "empty-frame", "skippable-only"])
def test_iter_dump_records_reports_a_missing_header_when_no_text_is_produced(kind):
    data = {"no-bytes": b"", "empty-frame": zstandard.ZstdCompressor().compress(b""),
            "skippable-only": skippable_frame()}[kind]
    assert_invalid_dump_is_reported(io.BytesIO(data))


@pytest.mark.parametrize("line", [None, [], "not an object", {}, {"content": [rec()]}])
def test_iter_dump_records_reports_invalid_top_level_envelopes(line):
    # Unknown typed objects are extensibility; missing envelopes are format errors.
    assert_invalid_dump_is_reported(zst_bytes([
        dump_line(dump_header()), dump_line(line), dump_line({"type": "Place", "content": [rec()]}),
    ]))


@pytest.mark.parametrize("first", [
    {"type": "CountryInfo", "content": {"data_timestamp": "2026-10-03T23:01:00Z"}},
    {"type": "Place", "content": [rec(object_id=99)]},
    [],
    {"type": "NominatimDumpFile", "content": []},
])
def test_iter_dump_records_reports_invalid_headers(first):
    # Regression: line zero must be a header, not merely valid JSON.
    assert_invalid_dump_is_reported(zst_bytes([
        dump_line(first), dump_line({"type": "Place", "content": [rec()]}),
    ]))


def test_iter_dump_records_counts_broken_header_without_promoting_next_line():
    stats, header = PlaceStats(), {}
    lines = ["{broken\n", dump_line({"type": "Place", "content": [rec()]})]
    assert list(iter_dump_records(zst_bytes(lines), stats, header)) == [rec()]
    assert header == {}
    assert stats.decode_errors == 1
    assert stats.non_place_lines == 0


@pytest.mark.parametrize("content", [{"unexpected": rec()}, "not an array"])
def test_iter_dump_records_reports_malformed_place_payloads(content):
    # Valid JSON with an invalid Place payload must not look like a clean dump.
    assert_invalid_dump_is_reported(zst_bytes([
        dump_line(dump_header()), dump_line({"type": "Place", "content": content}),
    ]))


def test_iter_dump_records_recovers_after_bad_json_and_ignores_unknown_types():
    stats, header = PlaceStats(), {}
    lines = [
        dump_line(dump_header()),
        dump_line({"type": "Place", "content": [rec()]}),
        "{broken\n",
        dump_line({"type": "generator:metadata", "content": [rec(object_id=99)]}),
        dump_line(None),
        dump_line({"type": "Place", "content": []}),
        dump_line({"type": "Place", "content": [rec(object_id=2)]}).rstrip("\n"),
    ]
    assert list(iter_dump_records(zst_bytes(lines), stats, header)) == [rec(), rec(object_id=2)]
    # Both broken JSON and a top-level null break the envelope format. The
    # generator-prefixed object remains a legitimate unknown type to ignore.
    assert (stats.decode_errors, stats.non_place_lines) == (2, 1)


def test_iter_dump_records_handles_long_unicode_lines_and_short_source_reads():
    class ShortReads(io.BytesIO):
        def read(self, size=-1):
            assert size > 0, "The compressed source must be read incrementally"
            return super().read(min(size, 7))

    record = rec(name={"name": "夢時代" * 20000})
    raw = dump_line(dump_header()) + dump_line({"type": "Place", "content": [record]})
    stream = ShortReads(zstandard.ZstdCompressor().compress(raw.encode("utf-8")))
    stats = PlaceStats()
    assert list(iter_dump_records(stream, stats, {})) == [record]
    assert stats.decode_errors == 0


def test_iter_dump_records_rejects_invalid_utf8():
    raw = dump_line(dump_header()).encode("utf-8") + b'\xff\n'
    stream = io.BytesIO(zstandard.ZstdCompressor().compress(raw))
    # Decoding happens while advancing TextIOWrapper, outside json.loads' try block.
    with pytest.raises(UnicodeDecodeError):
        list(iter_dump_records(stream, PlaceStats(), {}))


def test_iter_dump_records_rejects_a_corrupt_zstd_checksum():
    raw = (dump_line(dump_header()) + dump_line({"type": "Place", "content": [rec()]})).encode("utf-8")
    frame = zstandard.ZstdCompressor(write_checksum=True).compress(raw)
    corrupt = frame[:-1] + bytes([frame[-1] ^ 1])
    with pytest.raises(zstandard.ZstdError):
        list(iter_dump_records(io.BytesIO(corrupt), PlaceStats(), {}))


def test_iter_dump_records_reports_a_truncated_zstd_frame():
    raw = (dump_line(dump_header()) + dump_line({"type": "Place", "content": [rec()]})).encode("utf-8")
    frame = zstandard.ZstdCompressor(write_checksum=True).compress(raw)
    # Remove the checksum only: all JSON can still decode, but the frame is incomplete.
    assert_invalid_dump_is_reported(io.BytesIO(frame[:-4]))


def test_make_doc_normalizes_aliases_and_uses_only_supported_extra_names():
    doc = make_doc(rec(
        name={"name": "咖啡店", "name:en": " Cafe ; ; Cafe ", "alt_name": "咖啡店; 小店",
              "old_name": None, "name:ja": ["ignored"]},
        extra={"short_name": " 小店 ; Cafe", "official_name": " 正式名稱 ; 咖啡店 ",
               "brand": "not an alias", "wikidata": 123},
        address={"county": " 臺北市 ", "city": "\t", "suburb": " 大安區 ", "road": "ignored"},
    ), 0)
    assert doc["aliases"] == sorted(["Cafe", "小店", "正式名稱"])
    assert doc["where"] == "臺北市 大安區"
    assert doc["wikidata"] is None


def test_make_doc_preserves_m_name_whitespace_even_when_alias_is_trimmed_name():
    # Characterize the intentional M export behavior, including its redundant alias.
    doc = make_doc(rec(name={"name": " 咖啡店 ", "alt_name": "咖啡店"}), 0)
    assert doc["name"] == " 咖啡店 "
    assert doc["aliases"] == ["咖啡店"]


@pytest.mark.parametrize("centroid", [[-180, -90], [180, 90], [0, 0]])
def test_make_doc_accepts_coordinate_boundaries(centroid):
    assert make_doc(rec(centroid=centroid), 0)["_geo"] == {"lat": centroid[1], "lng": centroid[0]}


@pytest.mark.parametrize("value", [None, [], "not a mapping"])
def test_make_doc_handles_missing_optional_mappings(value):
    doc = make_doc(rec(extra=value, address=value), 0)
    assert doc["where"] == ""
    assert doc["aliases"] == []
    assert doc["wikidata"] is None


def test_build_documents_preserves_distinct_records_with_the_same_osm_id():
    records = [rec(), rec(name={"name": "另一名稱"}, osm_value="restaurant", centroid=[121.6, 25.1])]
    stats = PlaceStats()
    docs = list(build_documents(records, stats))
    assert [d["doc_id"] for d in docs] == ["N1-0", "N1-1"]
    assert [d["key"] for d in docs] == ["N1", "N1"]
    assert [d["name"] for d in docs] == ["咖啡店", "另一名稱"]
    assert [d["osm_value"] for d in docs] == ["cafe", "restaurant"]
    assert docs[1]["_geo"] == {"lat": 25.1, "lng": 121.6}
    assert stats.roads_merged_away == 0


@pytest.mark.parametrize("importances,winner", [([0.1, 0.5, 0.2], 1), ([0.5, 0.5, 0.1], 0)])
def test_build_documents_preserves_winner_fields_and_all_road_aliases(importances, winner):
    records = [
        rec(object_id=i + 1, osm_key="highway", osm_value=value,
            name={"name": "南迴公路", "alt_name": f"別名{i};共同別名;南迴公路"},
            importance=importance, centroid=[121 + i / 10, 25], extra={"wikidata": f"Q{i + 1}"})
        for i, (importance, value) in enumerate(zip(importances, ["trunk", "primary", "secondary"]))
    ]
    stats = PlaceStats()
    docs = list(build_documents(records, stats))
    expected = make_doc(records[winner], winner)
    expected["aliases"] = sorted(["別名0", "別名1", "別名2", "共同別名"])
    assert docs == [expected]
    assert (stats.records, stats.documents, stats.roads_merged_away) == (3, 1, 2)


@pytest.mark.parametrize("merge_roads,exclude,expected_keys,merged,excluded", [
    (False, True, ["N1", "N2"], 0, 1),
    (True, False, ["N1", "N3"], 1, 0),
])
def test_build_documents_evaluation_switches_are_independent(merge_roads, exclude, expected_keys, merged, excluded):
    records = [
        road(1, {"name": "南迴公路"}, {"city": "大武鄉"}),
        road(2, {"name": "南迴公路"}, {"city": "大武鄉"}),
        rec(object_id=3, osm_value="parcel_locker"),
    ]
    stats = PlaceStats()
    docs = list(build_documents(records, stats, merge_roads=merge_roads, exclude=exclude))
    assert sorted(d["key"] for d in docs) == expected_keys
    assert (stats.roads_merged_away, stats.excluded_category) == (merged, excluded)


def test_build_documents_counts_skipped_records_and_matches_exact_exclusion_pair():
    stats = PlaceStats()
    docs = list(build_documents([
        rec(name={}), rec(osm_value="parcel_locker"),
        rec(object_id=2, osm_key="shop", osm_value="parcel_locker"), rec(object_id=3),
    ], stats))
    assert [d["key"] for d in docs] == ["N2", "N3"]
    assert stats.as_dict() == {
        "records": 4, "decode_errors": 0, "non_place_lines": 0,
        "excluded_category": 1, "skipped": 1, "roads_merged_away": 0, "documents": 2,
    }


def test_write_ndjson_gz_accepts_an_empty_iterator(tmp_path):
    path = tmp_path / "empty.ndjson.gz"
    count, sha = write_ndjson_gz(iter(()), path)
    assert count == 0
    assert gzip.decompress(path.read_bytes()) == b""
    assert sha == hashlib.sha256(path.read_bytes()).hexdigest()


def test_zstd_reader_bounds_what_one_input_chunk_can_expand_to():
    # A zstd block decodes to at most 128 KiB and takes at least 4 bytes, so one read of
    # read_size compressed bytes can complete at most read_size / 4 blocks (plus one carried over).
    from search.places import _ZstdFrames

    sizes = []

    class Recording(_ZstdFrames):
        def _decompress(self, data):
            out = super()._decompress(data)
            sizes.append(len(out))
            return out

    raw = b"a" * (32 << 20)  # compresses about 30,000:1
    reader = Recording(io.BytesIO(zstandard.ZstdCompressor().compress(raw)), read_size=64)
    buf, total = bytearray(1 << 20), 0
    while n := reader.readinto(buf):
        total += n
    assert total == len(raw)
    assert max(sizes) <= (64 // 4 + 1) * (128 << 10)
