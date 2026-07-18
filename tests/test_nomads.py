from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from pipeline.cycles import Cycle
from pipeline.nomads import (
    cycle_is_complete,
    download_grib,
    filter_params,
    grib_filename,
    idx_url,
)

CYCLE = Cycle(datetime(2026, 7, 18, 6, tzinfo=timezone.utc))


def test_grib_filename():
    assert grib_filename(CYCLE, 0) == "gfs.t06z.pgrb2.0p25.f000"
    assert grib_filename(CYCLE, 48) == "gfs.t06z.pgrb2.0p25.f048"


def test_filter_params_variables_and_levels():
    p = filter_params(CYCLE, 3)
    assert p["dir"] == "/gfs.20260718/06/atmos"
    assert p["file"] == "gfs.t06z.pgrb2.0p25.f003"
    for key in ("var_UGRD", "var_VGRD", "var_PRATE",
                "lev_10_m_above_ground", "lev_surface"):
        assert p[key] == "on"


def test_idx_url():
    assert idx_url(CYCLE, 48) == (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"
        "gfs.20260718/06/atmos/gfs.t06z.pgrb2.0p25.f048.idx"
    )


def test_cycle_is_complete_checks_last_frame_idx():
    session = MagicMock()
    session.head.return_value = MagicMock(status_code=200)
    assert cycle_is_complete(CYCLE, session=session) is True
    session.head.assert_called_once_with(idx_url(CYCLE, 48), timeout=30)
    session.head.return_value = MagicMock(status_code=404)
    assert cycle_is_complete(CYCLE, session=session) is False


def test_cycle_is_complete_raises_on_server_error():
    session = MagicMock()
    session.head.return_value = MagicMock(status_code=503)
    with pytest.raises(RuntimeError):
        cycle_is_complete(CYCLE, session=session)


def test_download_retries_then_succeeds():
    session = MagicMock()
    ok = MagicMock(status_code=200, content=b"x" * 20_000)
    ok.raise_for_status = MagicMock()
    session.get.side_effect = [ConnectionError("boom"), ok]
    data = download_grib(CYCLE, 0, session=session, backoff=0)
    assert data == ok.content
    assert session.get.call_count == 2


def test_download_rejects_tiny_response_and_gives_up():
    session = MagicMock()
    tiny = MagicMock(status_code=200, content=b"error page")
    tiny.raise_for_status = MagicMock()
    session.get.return_value = tiny
    with pytest.raises(RuntimeError):
        download_grib(CYCLE, 0, session=session, backoff=0)
    assert session.get.call_count == 3
