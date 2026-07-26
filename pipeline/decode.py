from __future__ import annotations

import tempfile

import numpy as np
import xarray as xr

EXPECTED_SHAPE = (721, 1440)  # GFS 0.25°: lat 90..-90, lon 0..359.75


def shift_longitudes(grid: np.ndarray) -> np.ndarray:
    """經度 0..360 → -180..180（roll 半寬）。"""
    return np.roll(grid, grid.shape[1] // 2, axis=1)


def _open(path: str, filter_by_keys: dict) -> xr.Dataset:
    return xr.open_dataset(
        path,
        engine="cfgrib",
        backend_kwargs={"filter_by_keys": filter_by_keys, "indexpath": ""},
    )


def decode_frame(grib_bytes: bytes) -> dict[str, np.ndarray]:
    """GRIB2 → {'u','v','prate_mmh','temp_c'}，皆 float32 (721,1440)，lon 已轉 -180..180。

    網格形狀不符預期（GFS 改版）時直接 raise — fail loudly，不猜。
    """
    with tempfile.NamedTemporaryFile(suffix=".grib2") as f:
        f.write(grib_bytes)
        f.flush()
        wind = _open(f.name, {"typeOfLevel": "heightAboveGround", "level": 10})
        surface = _open(f.name, {"typeOfLevel": "surface", "stepType": "instant"})
        temp = _open(
            f.name,
            {"typeOfLevel": "heightAboveGround", "level": 2, "stepType": "instant"},
        )
        raw = {
            "u": wind["u10"].values,
            "v": wind["v10"].values,
            "prate_mmh": surface["prate"].values * 3600.0,  # kg/m²/s = mm/s → mm/h
            "temp_c": temp["t2m"].values - 273.15,  # K → °C
        }
    out: dict[str, np.ndarray] = {}
    for name, arr in raw.items():
        arr = np.asarray(arr, dtype=np.float32)
        if arr.shape != EXPECTED_SHAPE:
            raise ValueError(
                f"{name}: unexpected grid shape {arr.shape}, expected {EXPECTED_SHAPE}"
            )
        out[name] = shift_longitudes(arr)
    return out
