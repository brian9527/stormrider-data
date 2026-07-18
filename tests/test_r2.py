from pipeline.r2 import (
    CACHE_IMMUTABLE,
    CACHE_LATEST,
    LATEST_KEY,
    content_type_for,
    cycle_key,
    cycles_to_delete,
)


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
