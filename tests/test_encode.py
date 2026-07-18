import io

import numpy as np
from PIL import Image

from pipeline.encode import (
    PRECIP_MAX_MMH,
    WIND_MAX,
    WIND_MIN,
    decode_precip_pixel,
    decode_wind_pixel,
    encode_precip_png,
    encode_wind_png,
)


def test_wind_roundtrip_within_quantization_error():
    u = np.array([[-80.0, 0.0, 25.3], [79.9, -12.7, 60.0]], dtype=np.float32)
    v = np.array([[10.0, -33.3, 0.0], [-80.0, 5.5, 79.9]], dtype=np.float32)
    img = Image.open(io.BytesIO(encode_wind_png(u, v)))
    assert img.mode == "RGB"
    px = np.asarray(img).astype(np.float64)
    step = (WIND_MAX - WIND_MIN) / 255  # ~0.63 m/s
    assert np.allclose(decode_wind_pixel(px[..., 0]), u, atol=step / 2 + 1e-6)
    assert np.allclose(decode_wind_pixel(px[..., 1]), v, atol=step / 2 + 1e-6)


def test_wind_out_of_range_clipped():
    u = np.array([[-999.0, 999.0]], dtype=np.float32)
    v = np.zeros_like(u)
    px = np.asarray(Image.open(io.BytesIO(encode_wind_png(u, v))))
    assert px[0, 0, 0] == 0 and px[0, 1, 0] == 255


def test_precip_roundtrip_sqrt_scale():
    rate = np.array([[0.0, 0.1, 1.0], [5.0, 30.0, 100.0]], dtype=np.float32)
    img = Image.open(io.BytesIO(encode_precip_png(rate)))
    assert img.mode == "L"
    decoded = decode_precip_pixel(np.asarray(img).astype(np.float64))
    # sqrt scale：低值精度高、高值誤差放大 — 用相對+絕對混合容差
    assert np.allclose(decoded, rate, atol=0.05, rtol=0.05)


def test_precip_clips_at_max():
    rate = np.array([[500.0]], dtype=np.float32)
    px = np.asarray(Image.open(io.BytesIO(encode_precip_png(rate))))
    assert px[0, 0] == 255
    assert decode_precip_pixel(255) == PRECIP_MAX_MMH
