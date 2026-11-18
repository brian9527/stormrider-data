import pytest

from routing.checks import (CHECK_ROUTES, TILES_FLOOR_BYTES, CheckFailed, check_route, check_tiles_size,
                            checks_document, run_checks)
from routing.valhalla import NoRoute, ValhallaError

CHECK = {"name": "A→B", "from": [22.6, 120.3], "to": [22.9, 120.5], "km": [30.5, 59.8]}


class FakeClient:
    def __init__(self, km=44.0, classes=("primary",), no_route=False):
        self.km, self.classes, self.no_route, self.traced = km, list(classes), no_route, []

    def route(self, origin, destination, mode):
        if self.no_route:
            raise NoRoute("442")
        return {"km": self.km, "seconds": 3600.0, "shape": f"shape-{mode}"}

    def road_classes(self, shape, mode):
        self.traced.append((shape, mode))
        return self.classes


def test_a_good_scooter_route_passes_and_is_traced():
    client = FakeClient()
    problems, measured = check_route(client, CHECK, "scooter")
    assert problems == []
    assert measured == {"km": 44.0, "minutes": 60.0, "motorwayEdges": 0}
    assert client.traced == [("shape-scooter", "scooter")]


def test_car_routes_are_not_traced():
    client = FakeClient(classes=("motorway",))
    assert check_route(client, CHECK, "car")[0] == []
    assert client.traced == []


def test_a_scooter_route_on_a_motorway_fails():
    problems, _ = check_route(FakeClient(classes=("primary", "motorway")), CHECK, "scooter")
    assert problems == ["A→B (scooter): uses 1 motorway edges"]


def test_distance_outside_the_band_fails():
    assert "outside" in check_route(FakeClient(km=80.0), CHECK, "car")[0][0]


def test_no_route_fails():
    assert "no route" in check_route(FakeClient(no_route=True), CHECK, "car")[0][0]


def test_an_empty_trace_fails():
    assert "no edges" in check_route(FakeClient(classes=()), CHECK, "scooter")[0][0]


def test_edges_without_a_known_road_class_fail():
    # 〔計審 14〕a missing attribute must not pass as "not a motorway"
    problems, _ = check_route(FakeClient(classes=("primary", None, "bogus")), CHECK, "scooter")
    assert problems == ["A→B (scooter): 2 edges without a known road class"]


def test_run_checks_covers_every_route_in_both_modes():
    problems, report = run_checks(FakeClient(), [CHECK, {**CHECK, "name": "C→D"}])
    assert problems == []
    assert [(r["name"], r["mode"]) for r in report] == [("A→B", "scooter"), ("A→B", "car"),
                                                         ("C→D", "scooter"), ("C→D", "car")]


def test_the_published_list_has_six_named_routes_with_sane_bands():
    doc = checks_document()
    assert doc["version"] == 1 and doc["scooterTopSpeedKph"] == 60
    assert len(doc["routes"]) == 6 == len({r["name"] for r in CHECK_ROUTES})
    for r in CHECK_ROUTES:
        assert 0 < r["km"][0] < r["km"][1]
        assert all(-90 <= p[0] <= 90 and -180 <= p[1] <= 180 for p in (r["from"], r["to"]))


def test_tiles_size_floor_and_drop():
    check_tiles_size(TILES_FLOOR_BYTES, None)
    with pytest.raises(CheckFailed):
        check_tiles_size(TILES_FLOOR_BYTES - 1, None)
    check_tiles_size(850, 1000)
    with pytest.raises(CheckFailed):
        check_tiles_size(849, 1000)


@pytest.mark.parametrize("km,passes", [(30.5, True), (59.8, True), (30.499, False), (59.801, False)])
def test_distance_band_boundaries(km, passes):
    problems, _ = check_route(FakeClient(km=km), CHECK, "car")
    assert (not problems) == passes


@pytest.mark.parametrize("stage", ["route", "road_classes"])
@pytest.mark.parametrize("error", [NoRoute("442"), ValhallaError("unavailable")])
def test_run_checks_records_failures_and_continues(stage, error):
    class FailingClient(FakeClient):
        def route(self, origin, destination, mode):
            if stage == "route" and mode == "scooter":
                raise error
            return super().route(origin, destination, mode)

        def road_classes(self, shape, mode):
            if stage == "road_classes":
                raise error
            return super().road_classes(shape, mode)

    problems, report = run_checks(FailingClient(), [CHECK, {**CHECK, "name": "C→D"}])
    assert len(problems) == 2
    assert "A→B (scooter)" in problems[0] and "C→D (scooter)" in problems[1]
    assert len(report) == 4
    assert report[1]["km"] == report[3]["km"] == 44.0
    if stage == "road_classes":
        assert all("road class lookup failed" in p for p in problems)
        assert report[0]["km"] == 44.0
        assert "motorwayEdges" not in report[0]  # unverified is not zero motorways


def test_trace_failure_does_not_hide_a_distance_failure():
    class Untraceable(FakeClient):
        def road_classes(self, shape, mode):
            raise ValhallaError("443")

    problems, _ = check_route(Untraceable(km=80), CHECK, "scooter")
    assert len(problems) == 2
    assert "outside" in problems[0] and "road class lookup failed" in problems[1]
