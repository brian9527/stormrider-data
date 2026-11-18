import pytest
import requests

from routing.valhalla import COSTING, NoRoute, ValhallaClient, ValhallaError


class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses, self.posts, self.gets = list(responses), [], 0

    def post(self, url, json, timeout):
        self.posts.append((url, json))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, timeout):
        self.gets += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


TRIP = {"trip": {"summary": {"length": 44.25, "time": 3774.0}, "legs": [{"shape": "abc"}]}}


def test_route_sends_the_scooter_costing_and_reads_the_trip():
    s = FakeSession([FakeResponse(200, TRIP)])
    trip = ValhallaClient("http://v:8002/", session=s).route([22.6, 120.3], [22.9, 120.5], "scooter")
    url, body = s.posts[0]
    assert url == "http://v:8002/route"
    assert body["costing"] == "motor_scooter"
    assert body["costing_options"] == {"motor_scooter": {"top_speed": 60}}
    assert body["locations"] == [{"lat": 22.6, "lon": 120.3}, {"lat": 22.9, "lon": 120.5}]
    assert trip == {"km": 44.25, "seconds": 3774.0, "shape": "abc"}


def test_car_uses_auto_with_defaults():
    assert COSTING["car"] == ("auto", {})


@pytest.mark.parametrize("code", [154, 170, 171, 441, 442])
def test_no_route_codes_raise_no_route(code):
    s = FakeSession([FakeResponse(400, {"error_code": code})])
    with pytest.raises(NoRoute):
        ValhallaClient("http://v", session=s).route([0, 0], [1, 1], "car")


def test_other_errors_raise_valhalla_error():
    s = FakeSession([FakeResponse(500, ValueError("not json"))])
    with pytest.raises(ValhallaError):
        ValhallaClient("http://v", session=s).route([0, 0], [1, 1], "car")


def test_road_classes_use_edge_walk_on_the_route_shape():
    s = FakeSession([FakeResponse(200, {"edges": [{"road_class": "primary"}, {"road_class": "motorway"}]})])
    assert ValhallaClient("http://v", session=s).road_classes("abc", "scooter") == ["primary", "motorway"]
    _, body = s.posts[0]
    assert body["encoded_polyline"] == "abc" and body["shape_match"] == "edge_walk"
    assert body["filters"] == {"attributes": ["edge.road_class"], "action": "include"}


def test_wait_ready_retries_until_status_answers():
    s = FakeSession([requests.ConnectionError(), FakeResponse(503, {}), FakeResponse(200, {})])
    ValhallaClient("http://v", session=s).wait_ready(timeout_s=60, sleep=lambda _: None, clock=lambda: 0)
    assert s.gets == 3


def test_wait_ready_gives_up_at_the_deadline():
    s = FakeSession([requests.ConnectionError()] * 3)
    ticks = iter([0, 100, 200])
    with pytest.raises(ValhallaError):
        ValhallaClient("http://v", session=s).wait_ready(timeout_s=60, sleep=lambda _: None, clock=lambda: next(ticks))


@pytest.mark.parametrize("response", [
    requests.Timeout("slow"), requests.ConnectionError("reset"),
    FakeResponse(200, ValueError("bad json")), FakeResponse(200, []),
    FakeResponse(500, []), FakeResponse(400, {"error_code": []}),
])
def test_request_and_response_failures_are_valhalla_errors(response):
    client = ValhallaClient("http://v", session=FakeSession([response]))
    with pytest.raises(ValhallaError):
        client.route([0, 0], [1, 1], "car")


@pytest.mark.parametrize("trip", [
    None, {}, {"summary": {"length": 40, "time": 3600}},
    {"summary": {"length": 40, "time": 3600}, "legs": []},
    {"summary": {"length": 40, "time": 3600}, "legs": [{"shape": "a"}, {"shape": "b"}]},
    {"summary": {"length": 40, "time": 3600}, "legs": [{"shape": None}]},
    {"summary": {"length": 40, "time": 3600}, "legs": [{"shape": ""}]},
])
def test_malformed_trips_are_valhalla_errors(trip):
    client = ValhallaClient("http://v", session=FakeSession([FakeResponse(200, {"trip": trip})]))
    with pytest.raises(ValhallaError, match="malformed trip"):
        client.route([0, 0], [1, 1], "car")


@pytest.mark.parametrize("field", ["length", "time"])
@pytest.mark.parametrize("value", [None, True, "40", 0, -1, float("nan"), float("inf")])
def test_route_requires_positive_finite_measurements(field, value):
    summary = {"length": 40, "time": 3600, field: value}
    data = {"trip": {"summary": summary, "legs": [{"shape": "abc"}]}}
    client = ValhallaClient("http://v", session=FakeSession([FakeResponse(200, data)]))
    with pytest.raises(ValhallaError, match="malformed trip"):
        client.route([0, 0], [1, 1], "car")


@pytest.mark.parametrize("edges", [None, {}, [None], ["primary"], [{"road_class": []}]])
def test_malformed_trace_edges_are_valhalla_errors(edges):
    client = ValhallaClient("http://v", session=FakeSession([FakeResponse(200, {"edges": edges})]))
    with pytest.raises(ValhallaError):
        client.road_classes("abc", "scooter")


def test_exact_match_failure_is_not_a_successful_empty_trace():
    client = ValhallaClient("http://v", session=FakeSession([FakeResponse(400, {"error_code": 443})]))
    with pytest.raises(ValhallaError, match="443"):
        client.road_classes("abc", "scooter")


def test_missing_road_class_is_preserved_for_the_check_to_reject():
    client = ValhallaClient("http://v", session=FakeSession([FakeResponse(200, {"edges": [{}]})]))
    assert client.road_classes("abc", "scooter") == [None]
