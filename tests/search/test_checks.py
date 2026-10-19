import pytest

from search.checks import (PROBES, CheckFailed, check_counts, check_source_fresh, probe_passes, require_probes,
                           run_probes, source_date)


def test_first_publish_needs_the_floors():
    check_counts(400_000, 5_000, None)
    with pytest.raises(CheckFailed):
        check_counts(10, 5_000, None)
    with pytest.raises(CheckFailed):
        check_counts(400_000, 10, None)


def test_a_drop_over_ten_percent_stops_publishing():
    check_counts(91_000, 9_000, {"places": 100_000, "nearby": 10_000})
    with pytest.raises(CheckFailed):
        check_counts(89_000, 9_000, {"places": 100_000, "nearby": 10_000})
    with pytest.raises(CheckFailed):
        check_counts(91_000, 8_000, {"places": 100_000, "nearby": 10_000})


def test_probe_passes_on_name_or_alias_within_radius_in_the_top_three():
    probe = {"query": "九份老街", "names": ["九份老街", "基山街"], "ref": [25.1089, 121.8445], "radius_m": 1500}
    near = {"name": "基山街", "aliases": [], "_geo": {"lat": 25.1095, "lng": 121.8443}}
    far = {"name": "九份老街", "aliases": [], "_geo": {"lat": 23.0, "lng": 120.2}}
    alias = {"name": "九份", "aliases": ["九份老街"], "_geo": {"lat": 25.109, "lng": 121.845}}
    other = {"name": "別的", "aliases": [], "_geo": {"lat": 25.109, "lng": 121.845}}
    assert probe_passes(probe, [far, other, near])
    assert probe_passes(probe, [alias])
    assert not probe_passes(probe, [far, other, other, near])


def test_require_probes_wants_nine_of_ten():
    require_probes(9, 10)
    with pytest.raises(CheckFailed):
        require_probes(8, 10)


def test_there_are_ten_distinct_probes_with_complete_fields():
    assert len(PROBES) == 10
    assert len({p["query"] for p in PROBES}) == 10
    for p in PROBES:
        assert p["query"] and p["names"] and len(p["ref"]) == 2 and p["radius_m"] > 0


def test_source_date_takes_the_older_of_the_two_inputs():
    assert source_date("2026-10-30T21:00:02Z", "2026-10-28T23:01:00.000+00:00") == "2026-10-28"


def test_source_date_is_unknown_when_either_input_is():
    # An unreadable data time could be arbitrarily old, so it never drops out of the minimum.
    assert source_date(None, "2026-10-28T23:01:00Z") is None
    assert source_date("2026-10-30T21:00:02Z", "not a date") is None
    assert source_date() is None


def test_stale_sources_stop_publishing():
    check_source_fresh("2026-10-25", "2026-11-01T18:30:00+00:00")
    with pytest.raises(CheckFailed):
        check_source_fresh("2026-09-20", "2026-11-01T18:30:00+00:00")
    with pytest.raises(CheckFailed):
        check_source_fresh(None, "2026-11-01T18:30:00+00:00")


def test_exact_ten_percent_drop_is_allowed():
    check_counts(90_000, 9_000, {"places": 100_000, "nearby": 10_000})


def test_source_age_boundary_is_thirty_days():
    check_source_fresh("2026-10-02", "2026-11-01T18:30:00Z")
    with pytest.raises(CheckFailed, match="31 days old"):
        check_source_fresh("2026-10-01", "2026-11-01T18:30:00Z")


@pytest.mark.parametrize("geo", [None, {}, {"lat": 25}, {"lat": "25", "lng": 121}])
def test_probe_ignores_hits_without_numeric_coordinates(geo):
    probe = PROBES[0]
    assert not probe_passes(probe, [{"name": probe["names"][0], "_geo": geo}])


def test_run_probes_reports_failures_and_checks_every_query():
    queries = []

    def search(query):
        queries.append(query)
        probe = next(p for p in PROBES if p["query"] == query)
        if probe is PROBES[-1]:
            return []
        return [{"name": probe["names"][0], "_geo": {"lat": probe["ref"][0], "lng": probe["ref"][1]}}]

    assert run_probes(search) == (9, 10, [PROBES[-1]["query"]])
    assert queries == [p["query"] for p in PROBES]
