import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

# routing.main reads the PBF timestamp through search.nearby, which needs osmium. The weather workflow
# installs only requirements.txt and runs every test, so skip here like tests/search does.
pytest.importorskip("osmium")

import routing.checks
import routing.main as rmain
from routing.checks import CheckFailed
from search.checks import CheckFailed as SourceCheckFailed
from search.sources import GEOFABRIK_URL, ChecksumError


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    pbf = tmp_path / "taiwan.osm.pbf"
    pbf.write_bytes(b"pbf")
    # A small floor instead of a 300 MB fake graph; the floor itself is tested in test_routing_checks.
    monkeypatch.setattr(routing.checks, "TILES_FLOOR_BYTES", 4_000)
    state = {"problems": [], "tar_bytes": 5_000, "builds": 0}

    def fake_build(pbf_path, graph_dir, run=None):
        state["builds"] += 1
        graph_dir.mkdir(parents=True)
        tar = graph_dir / "valhalla_tiles.tar"
        with open(tar, "wb") as f:
            f.truncate(state["tar_bytes"])
        (graph_dir / "valhalla.json").write_text('{"mjolnir": {}}')
        return tar

    class FakeServing:
        def __init__(self, graph_dir, port):
            pass

        def __enter__(self):
            return "http://127.0.0.1:8002"

        def __exit__(self, *a):
            return False

    class FakeClient:
        def __init__(self, url):
            pass

        def wait_ready(self):
            pass

    monkeypatch.setattr(rmain, "build_graph", fake_build)
    monkeypatch.setattr(rmain, "serving", FakeServing)
    monkeypatch.setattr(rmain, "ValhallaClient", FakeClient)
    monkeypatch.setattr(rmain, "run_checks", lambda client: (state["problems"], [{"name": "A→B", "mode": "car"}]))
    monkeypatch.setattr(rmain, "pbf_timestamp", lambda p: "2026-11-01T20:21:06Z")
    return pbf, state


def test_local_publish_writes_the_r2_layout(tmp_path, fakes):
    pbf, _ = fakes
    site = tmp_path / "site"
    assert rmain.main(["--workdir", str(tmp_path / "w"), "--pbf-file", str(pbf), "--local-publish", str(site)]) == 0
    latest = json.loads((site / "routing" / "latest.json").read_text(encoding="utf-8"))
    folder = site / latest["manifest"].rsplit("/", 1)[0]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataDate"] == "2026-11-01"
    assert manifest["files"]["tiles"]["bytes"] == 5_000
    assert manifest["files"]["tiles"]["key"].endswith("/tiles.tar")
    assert manifest["valhallaImage"] == rmain.VALHALLA_IMAGE
    assert len(manifest["buildConfigSha256"]) == 64  # 〔計審 15〕
    assert json.loads((folder / "checks.json").read_text(encoding="utf-8"))["version"] == 1
    assert (folder / "LICENSE.md").read_text(encoding="utf-8").startswith("# StormRider routing graph")
    assert not list((tmp_path / "w").glob("graph-*"))


def test_failed_checks_publish_nothing(tmp_path, fakes):
    pbf, state = fakes
    state["problems"] = ["A→B (scooter): uses 3 motorway edges"]
    site = tmp_path / "site"
    with pytest.raises(CheckFailed):
        rmain.main(["--workdir", str(tmp_path / "w"), "--pbf-file", str(pbf), "--local-publish", str(site)])
    assert not site.exists()


def test_the_graph_folder_goes_after_a_success_and_stays_after_a_failure(tmp_path, fakes):
    pbf, state = fakes
    work = tmp_path / "w"
    assert rmain.main(["--workdir", str(work), "--pbf-file", str(pbf), "--skip-publish"]) == 0
    assert list(work.glob("graph-*")) == []  # the tar and the configuration were copied out first
    assert len(list(work.glob("out-*/tiles.tar"))) == 1
    state["problems"] = ["A→B (car): no route (442)"]
    with pytest.raises(CheckFailed):
        rmain.main(["--workdir", str(work), "--pbf-file", str(pbf), "--skip-publish"])
    assert len(list(work.glob("graph-*"))) == 1  # kept for a look at what went wrong


def test_a_too_small_graph_is_refused(tmp_path, fakes):
    pbf, state = fakes
    state["tar_bytes"] = 3_999
    with pytest.raises(CheckFailed):
        rmain.main(["--workdir", str(tmp_path / "w"), "--pbf-file", str(pbf), "--skip-publish"])
    assert len(list((tmp_path / "w").glob("graph-*/valhalla.json"))) == 1


def test_r2_publishing_is_refused_outside_github_actions(tmp_path, fakes, monkeypatch):
    pbf, _ = fakes
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(SystemExit):
        rmain.main(["--workdir", str(tmp_path / "w"), "--pbf-file", str(pbf), "--pbf-record", "x.json"])


def test_r2_publishing_needs_the_download_record(tmp_path, fakes, monkeypatch):
    pbf, _ = fakes
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(SystemExit):
        rmain.main(["--workdir", str(tmp_path / "w"), "--pbf-file", str(pbf)])


@pytest.fixture
def r2_run(tmp_path, fakes, monkeypatch):
    pbf, state = fakes
    record = {"url": GEOFABRIK_URL, "sha256": hashlib.sha256(b"pbf").hexdigest(),
              "md5": hashlib.md5(b"pbf").hexdigest(), "bytes": 3}
    rec = tmp_path / "pbf.json"
    rec.write_text(json.dumps(record), encoding="utf-8")
    clock = Mock()
    clock.now.return_value = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(rmain, "datetime", clock)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.setenv(name, "test-only")
    client = Mock()
    previous = Mock(return_value=None)
    publish = Mock()
    monkeypatch.setattr(rmain, "make_client", client)
    monkeypatch.setattr(rmain, "previous_tiles_bytes", previous)
    monkeypatch.setattr(rmain, "publish_routing", publish)
    return {"argv": ["--workdir", str(tmp_path / "w"), "--pbf-file", str(pbf), "--pbf-record", str(rec)],
            "record": record, "record_path": rec, "state": state,
            "client": client, "previous": previous, "publish": publish}


def test_r2_publish_keeps_verified_source_and_artifact_hashes(r2_run, tmp_path):
    run = r2_run
    assert rmain.main(run["argv"]) == 0
    run["publish"].assert_called_once()
    client, bucket, build_id, files, manifest = run["publish"].call_args.args
    assert client is run["client"].return_value and bucket == "test-only"
    assert build_id == manifest["buildId"] == "20261118T120000Z"
    assert manifest["sources"]["geofabrik"] == run["record"]
    for name, path in files.items():
        assert manifest["files"][name]["key"] == f"routing/{build_id}/{path.name}"
        assert manifest["files"][name]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    config = files["tiles"].parent / "build-config.json"
    assert manifest["buildConfigSha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
    run["previous"].assert_called_once_with(client, bucket)
    assert not list((tmp_path / "w").glob("graph-*"))


@pytest.mark.parametrize("reason", ["checks", "unknown_date", "stale", "future", "size_drop"])
def test_r2_publish_is_blocked_by_every_graph_gate(r2_run, monkeypatch, tmp_path, reason):
    run = r2_run
    if reason == "checks":
        run["state"]["problems"] = ["trace failed"]
    elif reason == "unknown_date":
        monkeypatch.setattr(rmain, "pbf_timestamp", lambda _: None)
    elif reason == "stale":
        monkeypatch.setattr(rmain, "pbf_timestamp", lambda _: "2026-10-01T00:00:00Z")
    elif reason == "future":
        monkeypatch.setattr(rmain, "pbf_timestamp", lambda _: "2099-01-01T00:00:00Z")
    else:
        # Above the first-build floor, but more than 15% smaller than the previous graph.
        run["previous"].return_value = 6_000
    with pytest.raises((CheckFailed, SourceCheckFailed)):
        rmain.main(run["argv"])
    run["publish"].assert_not_called()
    if reason != "size_drop":
        run["client"].assert_not_called()
    graphs = list((tmp_path / "w").glob("graph-*/valhalla.json"))
    assert len(graphs) == (0 if reason == "unknown_date" else 1)


@pytest.mark.parametrize("stage", ["previous", "publish"])
@pytest.mark.parametrize("error", [RuntimeError("R2 unavailable"), KeyboardInterrupt()])
def test_r2_failures_and_interruptions_keep_the_graph_and_staged_artifacts(r2_run, tmp_path, stage, error):
    r2_run[stage].side_effect = error
    with pytest.raises(type(error)) as caught:
        rmain.main(r2_run["argv"])
    assert caught.value is error
    work = tmp_path / "w"
    assert len(list(work.glob("graph-*/valhalla.json"))) == 1
    assert len(list(work.glob("out-*/tiles.tar"))) == 1


def test_graph_is_available_during_publication_and_removed_only_afterwards(r2_run, tmp_path):
    def publish(*args):
        assert len(list((tmp_path / "w").glob("graph-*/valhalla.json"))) == 1

    r2_run["publish"].side_effect = publish
    assert rmain.main(r2_run["argv"]) == 0
    r2_run["publish"].assert_called_once()
    assert not list((tmp_path / "w").glob("graph-*"))


def test_failed_local_publication_keeps_the_graph(tmp_path, fakes, monkeypatch):
    pbf, _ = fakes
    publish = Mock(side_effect=OSError("disk full"))
    monkeypatch.setattr(rmain, "local_publish", publish)
    with pytest.raises(OSError, match="disk full"):
        rmain.main(["--workdir", str(tmp_path / "w"), "--pbf-file", str(pbf),
                    "--local-publish", str(tmp_path / "site")])
    assert len(list((tmp_path / "w").glob("graph-*/valhalla.json"))) == 1


def test_undeletable_graph_warns_without_failing_a_completed_publish(r2_run, tmp_path, monkeypatch, capsys):
    # Simulate rmtree(ignore_errors=True) leaving root-owned directories on a Linux bind mount.
    remove = Mock()
    monkeypatch.setattr(rmain.shutil, "rmtree", remove)
    assert rmain.main(r2_run["argv"]) == 0
    r2_run["publish"].assert_called_once()
    graph, = (tmp_path / "w").glob("graph-*")
    remove.assert_called_once_with(graph, ignore_errors=True)
    assert f"warning: could not fully remove graph folder {graph}" in capsys.readouterr().out


@pytest.mark.parametrize("override", [{"md5": "0" * 32}, {"url": "https://example.com/taiwan.pbf"}, {"bytes": 4}])
def test_invalid_source_record_stops_before_build_or_r2(r2_run, override):
    run = r2_run
    run["record_path"].write_text(json.dumps({**run["record"], **override}), encoding="utf-8")
    with pytest.raises(ChecksumError):
        rmain.main(run["argv"])
    assert run["state"]["builds"] == 0
    run["client"].assert_not_called()
    run["publish"].assert_not_called()
