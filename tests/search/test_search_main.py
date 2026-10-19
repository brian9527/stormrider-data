import gzip
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

zstandard = pytest.importorskip("zstandard")
pytest.importorskip("osmium")

import search.main
import search.meili
from search.checks import CheckFailed
from search.main import build_outputs, local_publish, photon_timestamp
from search.meili import Meili, MeiliError

OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
 <node id="1" lat="22.6200" lon="120.2830" version="1"><tag k="amenity" v="motorcycle_parking"/></node>
 <node id="2" lat="22.6210" lon="120.2840" version="1"><tag k="railway" v="tram_stop"/><tag k="name" v="駁二大義站"/></node>
</osm>
"""

# What the stormrider-server parsers accept (src/search/updater.ts parseLatest / parseManifest /
# manifestMatches). A change on either side must keep these in step.
SERVER_BUILD_ID = re.compile(r"^\d{8}T\d{6}Z$")
SERVER_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _inputs(tmp_path, header_content):
    record = {"object_type": "N", "object_id": 9, "osm_key": "tourism", "osm_value": "attraction",
              "name": {"name": "駁二藝術特區"}, "centroid": [120.2821, 22.6198],
              "address": {"county": "高雄市", "city": "鹽埕區"}, "importance": 0.3}
    raw = json.dumps({"type": "NominatimDumpFile", "content": header_content}) + "\n" + \
        json.dumps({"type": "Place", "content": [record]}, ensure_ascii=False) + "\n"
    photon = tmp_path / "photon.jsonl.zst"
    photon.write_bytes(zstandard.ZstdCompressor().compress(raw.encode("utf-8")))
    pbf = tmp_path / "taiwan.osm"
    pbf.write_text(OSM_XML, encoding="utf-8")
    return photon, pbf


def test_photon_timestamp_reads_only_the_documented_field():
    assert photon_timestamp({"content.data_timestamp": "2026-10-03T23:01:00.000+00:00"}) == "2026-10-03T23:01:00.000+00:00"
    # Another date-like field (e.g. when the database was set up) is not the data time.
    assert photon_timestamp({"content.database_version": "2020-01-01", "content.data_timestamp": "x"}) is None
    assert photon_timestamp({}) is None


def test_build_outputs_and_local_publish(tmp_path, monkeypatch):
    # The XML fixture has no Geofabrik header, so the PBF time is supplied here.
    monkeypatch.setattr(search.main, "pbf_timestamp", lambda path: "2026-10-30T21:00:02Z")
    photon, pbf = _inputs(tmp_path, {"data_timestamp": "2026-10-28T23:01:00.000+00:00"})

    out = build_outputs(photon, pbf, tmp_path / "work")
    assert out["places"]["documents"] == 1 and out["nearby"]["documents"] == 2
    with gzip.open(out["places"]["path"], "rt", encoding="utf-8") as f:
        assert json.loads(f.readline())["name"] == "駁二藝術特區"
    assert out["dataDate"] == "2026-10-28"

    build_id = "20261101T183000Z"
    manifest = local_publish(tmp_path / "site", build_id, "2026-11-01T18:30:00+00:00", out,
                             {"photon": {}, "geofabrik": {}})
    latest = json.loads((tmp_path / "site" / "search" / "latest.json").read_text())
    prefix = f"search/{build_id}/"
    assert latest == {"buildId": build_id, "dataDate": "2026-10-28", "manifest": prefix + "manifest.json"}
    assert (tmp_path / "site" / manifest["files"]["probes"]["key"]).exists()
    # The server contract: same build and date in both files, every key inside this build's folder.
    assert SERVER_BUILD_ID.match(latest["buildId"]) and SERVER_DATE.match(latest["dataDate"])
    assert manifest["buildId"] == latest["buildId"] and manifest["dataDate"] == latest["dataDate"]
    assert all(entry["key"].startswith(prefix) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
               for entry in manifest["files"].values())
    assert isinstance(manifest["files"]["places"]["documents"], int)


def test_local_publish_refuses_an_unknown_data_time(tmp_path, monkeypatch):
    # The server rejects a build without a date, so a local build without one would only fail later.
    monkeypatch.setattr(search.main, "pbf_timestamp", lambda path: None)
    photon, pbf = _inputs(tmp_path, {"data_timestamp": "2026-10-28T23:01:00Z"})
    out = build_outputs(photon, pbf, tmp_path / "work")
    assert out["dataDate"] is None
    with pytest.raises(CheckFailed):
        local_publish(tmp_path / "site", "20261101T183000Z", "2026-11-01T18:30:00+00:00", out, {"photon": {}, "geofabrik": {}})
    assert not (tmp_path / "site").exists()


@pytest.fixture
def main_run(tmp_path, monkeypatch):
    # Exercise the CLI's actual gates, with every external boundary replaced.
    out = {
        "places": {"path": tmp_path / "places.ndjson.gz", "documents": 600_000, "sha256": "a" * 64},
        "nearby": {"path": tmp_path / "nearby.ndjson.gz", "documents": 5_000, "sha256": "b" * 64},
        "probes": {"path": tmp_path / "probes.json", "sha256": "c" * 64},
        "header": {}, "timestamps": {"photon": "2026-10-31", "geofabrik": "2026-10-30"},
        "dataDate": "2026-10-30", "stats": {"places": {"decode_errors": 0}, "nearby": {}},
    }
    mocks = {}
    for name in ("download", "build_outputs", "make_client", "previous_counts", "_probe_check", "publish", "local_publish"):
        mocks[name] = Mock(name=name)
        monkeypatch.setattr(search.main, name, mocks[name])
    mocks["download"].return_value = {"sha256": "d" * 64, "md5": "e" * 32, "bytes": 42}
    mocks["build_outputs"].return_value = out
    mocks["previous_counts"].return_value = {"places": 600_000, "nearby": 5_000}
    clock = Mock()
    clock.now.return_value = datetime(2026, 11, 1, 18, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(search.main, "datetime", clock)
    for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.setenv(name, "test-only")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")  # publishing only runs in the workflow
    return SimpleNamespace(out=out, argv=["--workdir", str(tmp_path / "work")], **mocks)


def test_main_refuses_to_publish_outside_the_workflow(main_run, monkeypatch):
    # R2 keys on a laptop must not make a second publisher (publish() and prune() assume one).
    monkeypatch.delenv("GITHUB_ACTIONS")
    with pytest.raises(SystemExit, match="only in the search-data workflow"):
        search.main.main(main_run.argv)
    main_run.download.assert_not_called()
    main_run.make_client.assert_not_called()
    main_run.publish.assert_not_called()


def test_local_runs_need_no_workflow(main_run, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS")
    assert search.main.main(main_run.argv + ["--skip-publish", "--skip-probes"]) == 0
    main_run.make_client.assert_not_called()


@pytest.mark.parametrize("reason", ["decode", "stale", "unknown_date", "places_drop", "nearby_drop", "probes"])
def test_main_never_publishes_when_a_gate_fails(main_run, reason):
    run = main_run
    messages = {"decode": "format in 7 places", "stale": "days old", "unknown_date": "unknown",
                "places_drop": "places dropped", "nearby_drop": "nearby dropped", "probes": "probes passed"}
    if reason == "decode":
        run.out["stats"]["places"]["decode_errors"] = 7
    elif reason == "stale":
        run.out["dataDate"] = "2026-09-01"
    elif reason == "unknown_date":
        run.out["dataDate"] = None
    elif reason == "places_drop":
        run.out["places"]["documents"] = 539_999
    elif reason == "nearby_drop":
        run.out["nearby"]["documents"] = 4_499
    else:
        run._probe_check.side_effect = CheckFailed("probes passed 8/10")
    with pytest.raises(CheckFailed, match=messages[reason]):
        search.main.main(run.argv)
    run.publish.assert_not_called()
    run.local_publish.assert_not_called()
    if reason != "probes":
        run._probe_check.assert_not_called()
    if reason in ("decode", "stale", "unknown_date"):
        run.make_client.assert_not_called()


def test_main_success_checks_probes_before_publishing_a_matching_manifest(main_run):
    run = main_run
    order = Mock()
    order.attach_mock(run._probe_check, "probe")
    order.attach_mock(run.publish, "publish")
    assert search.main.main(run.argv) == 0
    assert [c[0] for c in order.mock_calls] == ["probe", "publish"]
    run._probe_check.assert_called_once_with(run.out["places"]["path"])
    client, bucket, build_id, files, manifest = run.publish.call_args.args
    assert client is run.make_client.return_value and bucket == "test-only"
    assert build_id == manifest["buildId"] == "20261101T183000Z"
    assert manifest["dataDate"] == "2026-10-30"
    assert manifest["builtAt"] == "2026-11-01T18:30:00+00:00"
    assert files == {name: run.out[name]["path"] for name in ("places", "nearby", "probes")}
    assert manifest["sources"]["photon"]["md5"] == "e" * 32
    assert manifest["sources"]["geofabrik"]["timestamp"] == "2026-10-30"


def test_local_evaluation_skips_freshness_and_r2_and_forwards_variant_flags(main_run, tmp_path):
    run = main_run
    run.out["dataDate"] = "2020-01-01"
    assert search.main.main(run.argv + ["--local-publish", str(tmp_path / "site"),
                                      "--no-road-merge", "--keep-excluded", "--skip-probes"]) == 0
    assert run.build_outputs.call_args.kwargs == {"merge_roads": False, "exclude": False}
    run.local_publish.assert_called_once()
    run.make_client.assert_not_called()
    run.previous_counts.assert_not_called()
    run.publish.assert_not_called()
    run._probe_check.assert_not_called()


def test_local_evaluation_still_applies_first_publish_floors(main_run, tmp_path):
    main_run.out["places"]["documents"] = 10
    with pytest.raises(CheckFailed, match="first-publish floor"):
        search.main.main(main_run.argv + ["--local-publish", str(tmp_path / "site"), "--skip-probes"])
    main_run.local_publish.assert_not_called()
    main_run.make_client.assert_not_called()


def test_skip_publish_does_not_write_either_destination(main_run):
    assert search.main.main(main_run.argv + ["--skip-publish", "--skip-probes"]) == 0
    main_run.make_client.assert_not_called()
    main_run.publish.assert_not_called()
    main_run.local_publish.assert_not_called()


@pytest.mark.parametrize("passed", [8, 9])
def test_probe_check_waits_for_import_then_enforces_probe_threshold(tmp_path, monkeypatch, passed):
    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr(search.main, "Meili", factory)
    monkeypatch.setenv("MEILI_MASTER_KEY", "test-only")
    monkeypatch.setenv("MEILI_URL", "http://meili.invalid:7700")

    def probes(search):
        search("台北車站")
        return passed, 10, []

    monkeypatch.setattr(search.main, "run_probes", probes)
    path = tmp_path / "places.ndjson.gz"
    if passed == 8:
        with pytest.raises(CheckFailed, match="8/10"):
            search.main._probe_check(path)
    else:
        search.main._probe_check(path)
    assert client.mock_calls == [call.recreate_index("check_places", "doc_id"),
                                 call.update_settings("check_places", search.main.PLACES_SETTINGS),
                                 call.add_ndjson_gz("check_places", path),
                                 call.search("check_places", "台北車站")]


# The authorized test file list has no test_meili.py; keep the probe client's tests here.
class MeiliSession:
    def __init__(self, replies):
        self.headers = {}
        self.replies = iter(replies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        status, body = next(self.replies)
        return SimpleNamespace(status_code=status, content=json.dumps(body).encode(), json=lambda: body)


@pytest.mark.parametrize("n", [0, 10_000, 10_001])
def test_meili_batches_utf8_documents_and_waits_between_batches(tmp_path, n):
    path = tmp_path / "places.ndjson.gz"
    document = json.dumps({"doc_id": "N1-0", "name": "臺北車站"}, ensure_ascii=False)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for _ in range(n):
            f.write(document + "\n\n")  # blank lines do not count toward a batch
    sizes = [10_000] * (n // 10_000) + ([n % 10_000] if n % 10_000 else [])
    replies = []
    for task_id in range(len(sizes)):
        replies.extend([(202, {"taskUid": task_id}), (200, {"status": "succeeded"})])
    session = MeiliSession(replies)
    client = Meili("http://meili.invalid/", "test-only", session=session)
    assert client.add_ndjson_gz("check_places", path) == n
    assert session.headers["Authorization"] == "Bearer test-only"
    assert [c[0] for c in session.calls] == ["POST", "GET"] * len(sizes)
    for task_id, size in enumerate(sizes):
        method, url, options = session.calls[2 * task_id]
        assert url == "http://meili.invalid/indexes/check_places/documents"
        assert options["headers"] == {"Content-Type": "application/x-ndjson"}
        assert options["timeout"] == 300
        assert options["data"].decode("utf-8").splitlines() == [document] * size
        assert session.calls[2 * task_id + 1][1] == f"http://meili.invalid/tasks/{task_id}"


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_meili_stops_uploading_after_failed_batch(tmp_path, status):
    path = tmp_path / "places.ndjson.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write('{"doc_id":"1"}\n{"doc_id":"2"}\n')
    session = MeiliSession([(202, {"taskUid": 4}),
                            (200, {"status": status, "error": {"code": "invalid_document_id"}})])
    with pytest.raises(MeiliError, match="invalid_document_id"):
        Meili("http://meili.invalid", "key", session=session).add_ndjson_gz("check", path, batch_lines=1)
    assert len(session.calls) == 2


def test_meili_wait_polls_pending_tasks_until_success(monkeypatch):
    session = MeiliSession([(200, {"status": s}) for s in ("enqueued", "processing", "succeeded")])
    sleep = Mock()
    monkeypatch.setattr(search.meili.time, "sleep", sleep)
    monkeypatch.setattr(search.meili.time, "monotonic", lambda: 0)
    Meili("http://meili.invalid", "key", session=session).wait({"taskUid": 7})
    assert sleep.call_args_list == [call(1.0), call(1.0)]


def test_meili_wait_has_a_deadline_without_real_sleep(monkeypatch):
    session = MeiliSession([(200, {"status": "processing"})])
    monkeypatch.setattr(search.meili.time, "monotonic", Mock(side_effect=[0, 11]))
    sleep = Mock()
    monkeypatch.setattr(search.meili.time, "sleep", sleep)
    with pytest.raises(MeiliError, match="task_timeout"):
        Meili("http://meili.invalid", "key", session=session).wait({"taskUid": 7}, timeout_s=10)
    sleep.assert_not_called()


@pytest.mark.parametrize("async_missing", [False, True])
def test_meili_recreates_an_index_missing_at_delete_time(async_missing):
    missing = {"code": "index_not_found"}
    replies = ([(202, {"taskUid": 1}), (200, {"status": "failed", "error": missing})]
               if async_missing else [(404, missing)])
    replies += [(202, {"taskUid": 2}), (200, {"status": "succeeded"})]
    session = MeiliSession(replies)
    Meili("http://meili.invalid", "key", session=session).recreate_index("check", "doc_id")
    creates = [c for c in session.calls if c[0] == "POST"]
    assert len(creates) == 1
    assert creates[0][2]["json"] == {"uid": "check", "primaryKey": "doc_id"}


def test_meili_does_not_ignore_other_delete_errors():
    session = MeiliSession([(403, {"code": "invalid_api_key"})])
    with pytest.raises(MeiliError, match="invalid_api_key"):
        Meili("http://meili.invalid", "key", session=session).recreate_index("check", "doc_id")
    assert len(session.calls) == 1
