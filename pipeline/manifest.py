from __future__ import annotations

from datetime import datetime

from pipeline.cycles import FORECAST_HOURS, Cycle
from pipeline.encode import PRECIP_MAX_MMH, WIND_MAX, WIND_MIN


def _iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def build_manifest(cycle: Cycle) -> dict:
    return {
        "cycle": _iso_z(cycle.run),
        "resolution": [1440, 721],
        "bbox": [-180, -90, 180, 90],
        "variables": {
            "wind": {
                "encoding": "rg-linear",
                "min": WIND_MIN,
                "max": WIND_MAX,
                "unit": "m/s",
            },
            "precip": {
                "encoding": "gray-sqrt",
                "max": PRECIP_MAX_MMH,
                "unit": "mm/h",
            },
        },
        "frames": [
            {
                "hour": h,
                "validTime": _iso_z(cycle.valid_time(h)),
                "wind": f"wind_f{h:03d}.png",
                "precip": f"precip_f{h:03d}.png",
            }
            for h in FORECAST_HOURS
        ],
    }


def build_latest(cycle: Cycle) -> dict:
    return {"cycle": _iso_z(cycle.run), "path": f"gfs/{cycle.cycle_id}/"}
