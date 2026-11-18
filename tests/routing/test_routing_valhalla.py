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
        return self.responses.pop(0)

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


def test_no_route_codes_raise_no_route():
    s = FakeSession([FakeResponse(400, {"error_code": 442})])
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
