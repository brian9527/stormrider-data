import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from pipeline.cycles import FORECAST_HOURS
from pipeline.main import run

ENV = {
    "R2_ACCOUNT_ID": "acct",
    "R2_ACCESS_KEY_ID": "key",
    "R2_SECRET_ACCESS_KEY": "secret",
    "R2_BUCKET": "stormrider-weather",
}
NOW = datetime(2026, 7, 18, 10, 35, tzinfo=timezone.utc)  # → 最新候選 06Z


class FakeClientError(Exception):
    def __init__(self, code="404"):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def make_fakes(*, manifest_exists=False, complete=True):
    import numpy as np

    client = MagicMock()
    client.exceptions.ClientError = FakeClientError
    if manifest_exists:
        client.head_object.return_value = {}
        client.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(
                {"cycle": "2026-07-18T06:00Z", "path": "gfs/20260718T06/"}
            ).encode())
        }
    else:
        client.head_object.side_effect = FakeClientError("404")
    client.list_objects_v2.return_value = {"CommonPrefixes": [], "Contents": []}
    fields = {
        "u": np.zeros((721, 1440), dtype=np.float32),
        "v": np.zeros((721, 1440), dtype=np.float32),
        "prate_mmh": np.zeros((721, 1440), dtype=np.float32),
    }
    deps = {
        "client_factory": lambda *a: client,
        "check_complete": lambda cycle: complete,
        "download": lambda cycle, h: b"grib-bytes",
        "decode": lambda data: fields,
    }
    return client, deps


def uploaded_keys(client):
    return [c.kwargs["Key"] for c in client.put_object.call_args_list]


def test_publishes_full_cycle_then_latest_last():
    client, deps = make_fakes()
    result = run(NOW, ENV, **deps)
    assert result == "published 20260718T06"
    keys = uploaded_keys(client)
    # 17 wind + 17 precip + manifest + latest
    assert len(keys) == len(FORECAST_HOURS) * 2 + 2
    assert keys[-1] == "gfs/latest.json"
    assert keys[-2] == "gfs/20260718T06/manifest.json"
    latest_body = client.put_object.call_args_list[-1].kwargs["Body"]
    assert json.loads(latest_body)["path"] == "gfs/20260718T06/"


def test_skips_when_manifest_already_uploaded():
    client, deps = make_fakes(manifest_exists=True)
    assert run(NOW, ENV, **deps) == "skipped 20260718T06 (already published)"
    client.put_object.assert_not_called()


def test_skip_repairs_missing_latest():
    client, deps = make_fakes(manifest_exists=True)
    client.get_object.side_effect = FakeClientError("NoSuchKey")
    result = run(NOW, ENV, **deps)
    assert result == "skipped 20260718T06 (already published)"
    keys = uploaded_keys(client)
    assert keys == ["gfs/latest.json"]


def test_skip_does_not_touch_current_latest():
    client, deps = make_fakes(manifest_exists=True)
    run(NOW, ENV, **deps)
    client.put_object.assert_not_called()


def test_head_error_propagates():
    import pytest

    client, deps = make_fakes()
    client.head_object.side_effect = FakeClientError("403")
    with pytest.raises(FakeClientError):
        run(NOW, ENV, **deps)


def test_falls_back_to_previous_cycle_when_incomplete():
    client, deps = make_fakes()
    calls = []

    def check(cycle):
        calls.append(cycle.cycle_id)
        return cycle.cycle_id != "20260718T06"  # 06Z 未齊全 → 退 00Z

    deps["check_complete"] = check
    assert run(NOW, ENV, **deps) == "published 20260718T00"
    assert calls[0] == "20260718T06"


def test_no_complete_cycle_raises():
    import pytest

    client, deps = make_fakes(complete=False)
    with pytest.raises(RuntimeError):
        run(NOW, ENV, **deps)
