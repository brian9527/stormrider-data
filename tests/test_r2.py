from pipeline.r2 import (
    CACHE_IMMUTABLE,
    CACHE_LATEST,
    LATEST_KEY,
    content_type_for,
    cycle_key,
    cycles_to_delete,
)


class FakeClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def make_client(exc=None, body=None):
    from unittest.mock import MagicMock

    client = MagicMock()
    client.exceptions.ClientError = FakeClientError
    if exc is not None:
        client.head_object.side_effect = exc
        client.get_object.side_effect = exc
    elif body is not None:
        client.get_object.return_value = {"Body": MagicMock(read=lambda: body)}
    return client


def test_keys():
    assert cycle_key("20260718T06", "wind_f000.png") == "gfs/20260718T06/wind_f000.png"
    assert LATEST_KEY == "gfs/latest.json"


def test_content_types():
    assert content_type_for("wind_f000.png") == "image/png"
    assert content_type_for("manifest.json") == "application/json"


def test_cache_headers():
    assert CACHE_IMMUTABLE == "public, max-age=86400, immutable"
    assert CACHE_LATEST == "public, max-age=300"


def test_cycles_to_delete_keeps_newest_and_current():
    existing = ["20260716T18", "20260717T00", "20260717T06", "20260717T12"]
    # keep=3：保留最新 3 輪（字典序 = 時間序），刪最舊
    assert cycles_to_delete(existing, keep=3) == ["20260716T18"]
    assert cycles_to_delete(["20260718T00"], keep=3) == []


def test_object_exists_404_is_false_other_errors_raise():
    import pytest

    from pipeline.r2 import object_exists

    assert object_exists(make_client(exc=FakeClientError("404")), "b", "k") is False
    assert object_exists(make_client(exc=FakeClientError("NotFound")), "b", "k") is False
    with pytest.raises(FakeClientError):
        object_exists(make_client(exc=FakeClientError("403")), "b", "k")


def test_get_json_returns_none_on_missing_and_parses_body():
    import pytest

    from pipeline.r2 import get_json

    assert get_json(make_client(exc=FakeClientError("NoSuchKey")), "b", "k") is None
    assert get_json(make_client(body=b'{"a": 1}'), "b", "k") == {"a": 1}
    with pytest.raises(FakeClientError):
        get_json(make_client(exc=FakeClientError("500")), "b", "k")
