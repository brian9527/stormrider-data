from pathlib import Path

import numpy as np
import pytest

from pipeline.decode import EXPECTED_SHAPE, decode_frame, shift_longitudes

FIXTURE = Path(__file__).parent / "fixtures" / "sample.grib2"


def test_shift_longitudes_rolls_half_width():
    grid = np.arange(8, dtype=np.float32).reshape(2, 4)  # lon 0,90,180,270
    shifted = shift_longitudes(grid)
    assert shifted.tolist() == [[2, 3, 0, 1], [6, 7, 4, 5]]


def test_decode_frame_shapes_and_ranges():
    fields = decode_frame(FIXTURE.read_bytes())
    assert set(fields) == {"u", "v", "prate_mmh", "temp_c"}
    for name, arr in fields.items():
        assert arr.shape == EXPECTED_SHAPE, name
        assert arr.dtype == np.float32, name
        assert np.isfinite(arr).all(), name
    assert np.abs(fields["u"]).max() < 150
    assert np.abs(fields["v"]).max() < 150
    assert fields["prate_mmh"].min() >= 0
    assert fields["prate_mmh"].max() < 500  # mm/h 合理上限
    assert fields["temp_c"].min() > -90  # 南極內陸極端低溫下限
    assert fields["temp_c"].max() < 60  # 地表實測極端高溫上限
    # fixture 的原始 (lat=100, lon=200) 2m TMP 是 286.10083 K；roll 後在 x=920。
    # 同點 surface TMP 是 285.92550 K，故此斷言也防止選錯垂直層。
    assert fields["temp_c"][100, 920] == pytest.approx(12.950836, abs=1e-5)


def test_decode_frame_handles_dual_steptype_prate():
    # f003+ 的 PRATE 同時有 instant 與 avg 兩種 stepType — regression for the
    # DatasetBuildError seen on the first real Actions run
    fixture = Path(__file__).parent / "fixtures" / "sample_f003.grib2"
    fields = decode_frame(fixture.read_bytes())
    assert fields["prate_mmh"].shape == EXPECTED_SHAPE
    assert fields["prate_mmh"].min() >= 0
    assert fields["temp_c"].shape == EXPECTED_SHAPE
    # 原始北極 x=0 的 instant/avg PRATE 分別為 0.21024/0.0648 mm/h；
    # roll 後 x=720，明確驗證 decode 選到 instant message。
    assert fields["prate_mmh"][0, 720] == pytest.approx(0.21024, abs=1e-6)
    assert fields["temp_c"][100, 920] == pytest.approx(13.862579, abs=1e-5)


def test_decode_frame_rejects_wrong_shape():
    with pytest.raises(Exception):
        decode_frame(b"not a grib file")
