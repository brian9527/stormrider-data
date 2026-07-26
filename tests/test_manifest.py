from datetime import datetime, timezone

from pipeline.cycles import FORECAST_HOURS, Cycle
from pipeline.manifest import build_latest, build_manifest

CYCLE = Cycle(datetime(2026, 7, 18, 6, tzinfo=timezone.utc))


def test_manifest_structure():
    m = build_manifest(CYCLE)
    assert m["cycle"] == "2026-07-18T06:00Z"
    assert m["resolution"] == [1440, 721]
    assert m["bbox"] == [-180, -90, 180, 90]
    assert set(m["variables"]) == {"wind", "precip", "temp"}
    assert m["variables"]["wind"] == {
        "encoding": "rg-linear", "min": -80.0, "max": 80.0, "unit": "m/s",
    }
    assert m["variables"]["precip"] == {
        "encoding": "gray-sqrt", "max": 100.0, "unit": "mm/h",
    }
    assert m["variables"]["temp"] == {
        "encoding": "linear", "min": -60.0, "max": 50.0, "unit": "°C",
    }
    assert len(m["frames"]) == len(FORECAST_HOURS)
    f0 = m["frames"][0]
    assert f0 == {
        "hour": 0, "validTime": "2026-07-18T06:00Z",
        "wind": "wind_f000.png", "precip": "precip_f000.png", "temp": "temp_f000.png",
    }
    assert m["frames"][-1]["hour"] == 48
    assert m["frames"][-1]["validTime"] == "2026-07-20T06:00Z"
    for frame, hour in zip(m["frames"], FORECAST_HOURS, strict=True):
        assert set(frame) == {"hour", "validTime", "wind", "precip", "temp"}
        assert frame["wind"] == f"wind_f{hour:03d}.png"
        assert frame["precip"] == f"precip_f{hour:03d}.png"
        assert frame["temp"] == f"temp_f{hour:03d}.png"


def test_latest_pointer():
    assert build_latest(CYCLE) == {
        "cycle": "2026-07-18T06:00Z", "path": "gfs/20260718T06/",
    }
