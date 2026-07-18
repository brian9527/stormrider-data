from __future__ import annotations

import time

import requests

from pipeline.cycles import Cycle

FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
PUB_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
LAST_FORECAST_HOUR = 48
MIN_GRIB_BYTES = 10_000  # filter 端點錯誤時回小型 HTML，不會有這麼小的正常 GRIB


def grib_filename(cycle: Cycle, forecast_hour: int) -> str:
    return f"gfs.t{cycle.hour_str}z.pgrb2.0p25.f{forecast_hour:03d}"


def filter_params(cycle: Cycle, forecast_hour: int) -> dict[str, str]:
    return {
        "dir": f"/gfs.{cycle.date_str}/{cycle.hour_str}/atmos",
        "file": grib_filename(cycle, forecast_hour),
        "var_UGRD": "on",
        "var_VGRD": "on",
        "var_PRATE": "on",
        "lev_10_m_above_ground": "on",
        "lev_surface": "on",
    }


def idx_url(cycle: Cycle, forecast_hour: int) -> str:
    return (
        f"{PUB_URL}/gfs.{cycle.date_str}/{cycle.hour_str}/atmos/"
        f"{grib_filename(cycle, forecast_hour)}.idx"
    )


def cycle_is_complete(cycle: Cycle, session=requests) -> bool:
    """最後一個預報檔 (f048) 的 .idx 存在 = 整輪已發布齊全。

    200 → True；404 → False；其他狀態（403/5xx/429）→ raise，fail loudly，
    不可誤判成「未齊全」而退到舊輪次。
    """
    resp = session.head(idx_url(cycle, LAST_FORECAST_HOUR), timeout=30)
    if resp.status_code == 200:
        return True
    if resp.status_code == 404:
        return False
    raise RuntimeError(f"NOMADS completeness check failed: HTTP {resp.status_code}")


def download_grib(
    cycle: Cycle,
    forecast_hour: int,
    session=requests,
    retries: int = 3,
    backoff: float = 5.0,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = session.get(
                FILTER_URL, params=filter_params(cycle, forecast_hour), timeout=120
            )
            resp.raise_for_status()
            if len(resp.content) < MIN_GRIB_BYTES:
                raise ValueError(
                    f"suspiciously small response ({len(resp.content)} bytes)"
                )
            return resp.content
        except Exception as e:  # noqa: BLE001 — 重試涵蓋網路/HTTP/內容檢查
            last_error = e
            if attempt < retries - 1:
                time.sleep(backoff * 2**attempt)
    raise RuntimeError(
        f"download {grib_filename(cycle, forecast_hour)} failed after {retries} attempts"
    ) from last_error
