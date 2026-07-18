from datetime import datetime, timezone

import pytest

from pipeline.cycles import FORECAST_HOURS, Cycle, candidate_cycles


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_forecast_hours_are_17_frames_every_3h():
    assert FORECAST_HOURS == tuple(range(0, 49, 3))
    assert len(FORECAST_HOURS) == 17


def test_cycle_id_and_paths():
    c = Cycle(utc(2026, 7, 18, 6))
    assert c.cycle_id == "20260718T06"
    assert c.date_str == "20260718"
    assert c.hour_str == "06"


def test_valid_time():
    c = Cycle(utc(2026, 7, 18, 6))
    assert c.valid_time(48) == utc(2026, 7, 20, 6)


def test_candidates_respect_publication_lag():
    # 10:29 UTC → 06Z 還沒過 +4.5h lag，最新候選應是 00Z
    cycles = candidate_cycles(utc(2026, 7, 18, 10, 29))
    assert [c.cycle_id for c in cycles] == ["20260718T00", "20260717T18", "20260717T12"]
    # 10:31 UTC → 06Z 已過 lag
    cycles = candidate_cycles(utc(2026, 7, 18, 10, 31))
    assert [c.cycle_id for c in cycles] == ["20260718T06", "20260718T00", "20260717T18"]


def test_candidates_cross_midnight():
    cycles = candidate_cycles(utc(2026, 7, 18, 2, 0))
    assert [c.cycle_id for c in cycles] == ["20260717T18", "20260717T12", "20260717T06"]


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        candidate_cycles(datetime(2026, 7, 18, 10, 0))
