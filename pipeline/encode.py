from __future__ import annotations

import io

import numpy as np
from PIL import Image

# scale 常數 — 同步寫進 manifest（見 pipeline/manifest.py），client 以 manifest 為準
WIND_MIN = -80.0  # m/s
WIND_MAX = 80.0
PRECIP_MAX_MMH = 100.0


def _to_png(pixels: np.ndarray, mode: str) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(pixels, mode=mode).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _quantize_linear(values: np.ndarray, lo: float, hi: float) -> np.ndarray:
    scaled = (np.clip(values, lo, hi) - lo) / (hi - lo) * 255.0
    return np.round(scaled).astype(np.uint8)


def encode_wind_png(u: np.ndarray, v: np.ndarray) -> bytes:
    """R=u, G=v（線性 ±80 m/s → 0..255），B=0。"""
    r = _quantize_linear(u, WIND_MIN, WIND_MAX)
    g = _quantize_linear(v, WIND_MIN, WIND_MAX)
    rgb = np.stack([r, g, np.zeros_like(r)], axis=-1)
    return _to_png(rgb, mode="RGB")


def decode_wind_pixel(pixel):
    return pixel / 255.0 * (WIND_MAX - WIND_MIN) + WIND_MIN


def encode_precip_png(rate_mmh: np.ndarray) -> bytes:
    """灰階，sqrt 映射保低雨量精度：pixel = sqrt(rate/max)*255。"""
    clipped = np.clip(rate_mmh, 0.0, PRECIP_MAX_MMH)
    pixels = np.round(np.sqrt(clipped / PRECIP_MAX_MMH) * 255.0).astype(np.uint8)
    return _to_png(pixels, mode="L")


def decode_precip_pixel(pixel):
    return (pixel / 255.0) ** 2 * PRECIP_MAX_MMH


TEMP_MIN_C = -60.0
TEMP_MAX_C = 50.0


def encode_temp_png(temp_c: np.ndarray) -> bytes:
    """灰階，線性映射：pixel = (temp_c - min) / (max - min) * 255。"""
    pixels = _quantize_linear(temp_c, TEMP_MIN_C, TEMP_MAX_C)
    return _to_png(pixels, mode="L")


def decode_temp_pixel(pixel):
    return pixel / 255.0 * (TEMP_MAX_C - TEMP_MIN_C) + TEMP_MIN_C
