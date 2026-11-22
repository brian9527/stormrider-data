import hashlib
import json
from pathlib import Path

import pytest
import requests

import mapstyle.main as main_mod
from mapstyle.checks import MAX_BYTES, StyleCheckFailed
from mapstyle.main import build, build_manifest, content_hash, fetch_style
from mapstyle.publish import LATEST_KEY, publish_map_styles
from search.publish import AlreadyPublished

FIXTURES = Path(__file__).parent / "fixtures"


class FakeS3:
    """Same fake as tests/search/test_publish.py (tests/ is not a package), plus the order of writes."""

    class exceptions:
        class ClientError(Exception):
            def __init__(self, code):
                self.response = {"Error": {"Code": code}}

    def __init__(self):
        self.objects, self.deleted, self.writes = {}, [], []

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        self.objects[Key] = {"body": Body, "type": ContentType, "cache": CacheControl}
        self.writes.append(Key)

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.ClientError("404")
        return {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.ClientError("NoSuchKey")
        body = self.objects[Key]["body"]

        class B:
            def read(self_inner):
                return body

        return {"Body": B()}

    def list_objects_v2(self, Bucket, Prefix, Delimiter=None, MaxKeys=1000):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if Delimiter:
            prefixes = sorted({Prefix + k[len(Prefix):].split("/")[0] + "/" for k in keys if "/" in k[len(Prefix):]})
            return {"CommonPrefixes": [{"Prefix": p} for p in prefixes]}
        return {"Contents": [{"Key": k} for k in keys[:MaxKeys]]} if keys else {}

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.deleted.append(o["Key"])
            self.objects.pop(o["Key"], None)


def fake_fetch(name):
    raw = (FIXTURES / f"style-{name}.json").read_bytes()
    return json.loads(raw), raw


@pytest.fixture
def in_actions(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for key in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.setenv(key, "test-only")
    monkeypatch.setattr(main_mod, "fetch_style", fake_fetch)
    s3 = FakeS3()
    monkeypatch.setattr(main_mod, "make_client", lambda *a: s3)
    return s3


def test_build_transforms_checks_and_records_both_styles():
    built = build(fake_fetch)
    assert set(built) == {"liberty", "fiord"}
    for name, b in built.items():
        assert "stormrider:attribution" in json.loads(b["body"])["metadata"]
        assert b["upstreamSha256"] == hashlib.sha256((FIXTURES / f"style-{name}.json").read_bytes()).hexdigest()
        assert b["upstreamUrl"] == f"https://tiles.openfreemap.org/styles/{name}"


def test_a_failed_check_stops_the_whole_build():
    def broken(name):
        style, raw = fake_fetch(name)
        if name == "fiord":
            style["version"] = 7
        return style, raw

    with pytest.raises(StyleCheckFailed, match="fiord"):
        build(broken)


def test_content_hash_depends_only_on_the_published_bytes():
    a, b = build(fake_fetch), build(fake_fetch)
    assert content_hash(a) == content_hash(b)
    b["fiord"]["upstreamSha256"] = "0" * 64
    assert content_hash(a) == content_hash(b)
    b["fiord"]["body"] = b["fiord"]["body"] + b" "
    assert content_hash(a) != content_hash(b)


def test_attribution_changes_affect_the_content_hash(monkeypatch):
    import mapstyle.transform as transform_mod

    before = build(fake_fetch)
    monkeypatch.setattr(transform_mod, "MODIFICATIONS", ["StormRider 修改：中文地名；新的修改註記"])
    after = build(fake_fetch)
    assert content_hash(before) != content_hash(after)
    for name in before:
        assert before[name]["upstreamSha256"] == after[name]["upstreamSha256"]


def test_manifest_lists_both_styles_under_the_build():
    built = build(fake_fetch)
    m = build_manifest("20261201T190000Z", "2026-12-01T19:00:00+00:00", built)
    assert m["buildId"] == "20261201T190000Z" and m["dataDate"] == "2026-12-01"
    assert m["contentSha256"] == content_hash(built)
    for name in ("liberty", "fiord"):
        entry = m["styles"][name]
        assert entry["key"] == f"map/20261201T190000Z/{name}.json"
        assert entry["sha256"] == hashlib.sha256(built[name]["body"]).hexdigest()
        assert entry["bytes"] == len(built[name]["body"])
        assert entry["upstream"]["sha256"] == built[name]["upstreamSha256"]


class FakeResponse:
    def __init__(self, chunks):
        self.chunks, self.read, self.closed = chunks, 0, False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    def close(self):
        self.closed = True


def test_fetch_stops_reading_an_oversized_upstream_part_way():
    response = FakeResponse([b"x" * (MAX_BYTES // 2), b"x" * (MAX_BYTES // 2), b"x", b"never read"])

    class Session:
        def get(self, url, headers, timeout, stream):
            assert stream
            return response

    with pytest.raises(StyleCheckFailed, match="over"):
        fetch_style("liberty", session=Session())
    assert response.read == 3 and response.closed


def test_publishing_runs_only_in_the_workflow(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(SystemExit):
        main_mod.main([])


def test_out_writes_the_styles_and_licence_without_touching_r2(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(main_mod, "fetch_style", fake_fetch)
    monkeypatch.setattr(main_mod, "make_client", lambda *a: pytest.fail("no R2 with --out"))
    assert main_mod.main(["--out", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["LICENSE.md", "fiord.json", "liberty.json"]
    assert json.loads((tmp_path / "liberty.json").read_bytes())["metadata"]["stormrider:attribution"]["style"] == "liberty"


def test_changed_content_publishes_the_files_then_latest_last(in_actions):
    assert main_mod.main([]) == 0
    assert in_actions.writes[-1] == LATEST_KEY
    latest = json.loads(in_actions.objects[LATEST_KEY]["body"])
    prefix = f"map/{latest['buildId']}/"
    assert set(in_actions.writes[:-1]) == {prefix + n for n in ("liberty.json", "fiord.json", "LICENSE.md", "manifest.json")}


def test_unchanged_content_publishes_nothing(in_actions):
    assert main_mod.main([]) == 0
    first = list(in_actions.writes)
    assert main_mod.main([]) == 0
    assert in_actions.writes == first


@pytest.mark.parametrize("failure", ["http", "read", "json"])
def test_failed_download_closes_response_and_never_publishes(in_actions, monkeypatch, failure):
    class Response(FakeResponse):
        def raise_for_status(self):
            if failure == "http":
                raise requests.HTTPError("503 upstream unavailable")

        def iter_content(self, size):
            if failure == "read":
                yield b'{"version":'
                raise requests.ConnectionError("read timed out")
            yield from super().iter_content(size)

    response = Response([b"<html>upstream error</html>"])

    class Session:
        def get(self, url, headers, timeout, stream):
            assert timeout == 60 and stream is True
            return response

    monkeypatch.setattr(main_mod, "fetch_style", lambda name: fetch_style(name, session=Session()))
    error = {"http": requests.HTTPError, "read": requests.ConnectionError,
             "json": json.JSONDecodeError}[failure]
    with pytest.raises(error):
        main_mod.main([])
    assert response.closed
    if failure == "http":
        assert response.read == 0
    assert in_actions.writes == [] and in_actions.deleted == []


def test_connect_timeout_never_publishes(in_actions, monkeypatch):
    class Session:
        def get(self, url, **kwargs):
            raise requests.ConnectTimeout("connect timed out")

    monkeypatch.setattr(main_mod, "fetch_style",
                        lambda name: fetch_style(name, session=Session()))
    with pytest.raises(requests.ConnectTimeout):
        main_mod.main([])
    assert in_actions.writes == [] and in_actions.deleted == []


def test_second_style_check_failure_never_touches_r2(in_actions, monkeypatch):
    def broken(name):
        style, raw = fake_fetch(name)
        if name == "fiord":
            style["version"] = 7
        return style, raw

    monkeypatch.setattr(main_mod, "fetch_style", broken)
    monkeypatch.setattr(main_mod, "make_client", lambda *args: pytest.fail("build failed before R2"))
    with pytest.raises(StyleCheckFailed, match="fiord"):
        main_mod.main([])
    assert in_actions.writes == []


def map_files(tmp_path):
    files = {}
    for name in ("liberty", "fiord"):
        path = tmp_path / f"{name}.json"
        path.write_bytes(b"{}")
        files[name] = path
    return files


@pytest.mark.parametrize("failed_name", ["liberty.json", "fiord.json", "LICENSE.md", "manifest.json", "latest.json"])
def test_map_upload_failure_keeps_old_pointer_and_does_not_prune(tmp_path, monkeypatch, failed_name):
    s3 = FakeS3()
    old_id, new_id = "20261122T010000Z", "20261122T020000Z"
    files = map_files(tmp_path)
    publish_map_styles(s3, "bucket", old_id, files, {"dataDate": "2026-11-22"})
    s3.objects["map/20261022T010000Z/liberty.json"] = {"body": b"partial"}
    old_objects = dict(s3.objects)
    original_put = s3.put_object

    def failing_put(**kwargs):
        if kwargs["Key"].rsplit("/", 1)[-1] == failed_name:
            raise OSError("upload failed")
        original_put(**kwargs)

    monkeypatch.setattr(s3, "put_object", failing_put)
    with pytest.raises(OSError, match="upload failed"):
        publish_map_styles(s3, "bucket", new_id, files, {"dataDate": "2026-11-22"})
    assert {key: s3.objects[key] for key in old_objects} == old_objects
    assert s3.deleted == []


def test_map_same_day_builds_keep_six_and_leave_other_roots_untouched(tmp_path):
    s3 = FakeS3()
    others = {f"{root}/{suffix}": {"body": b"untouched"}
              for root in ("search", "routing", "gfs")
              for suffix in ("latest.json", "20260101T000000Z/manifest.json")}
    s3.objects.update(others)
    files = map_files(tmp_path)
    for hour in range(1, 8):
        build_id = f"20261122T{hour:02d}0000Z"
        publish_map_styles(s3, "bucket", build_id, files, {"dataDate": "2026-11-22"})
    assert {key.split("/")[1] for key in s3.objects if key.startswith("map/2")} == {
        f"20261122T{hour:02d}0000Z" for hour in range(2, 8)}
    assert {key: s3.objects[key] for key in others} == others


@pytest.mark.parametrize("existing", ["liberty.json", "manifest.json"])
def test_map_refuses_to_overwrite_even_a_partial_build(tmp_path, existing):
    s3 = FakeS3()
    build_id = "20261122T010000Z"
    key = f"map/{build_id}/{existing}"
    s3.objects[key] = {"body": b"original"}
    with pytest.raises(AlreadyPublished):
        publish_map_styles(s3, "bucket", build_id, map_files(tmp_path), {"dataDate": "2026-11-22"})
    assert s3.objects == {key: {"body": b"original"}}
    assert s3.writes == [] and s3.deleted == []


def test_prune_failure_happens_after_the_new_pointer_is_live(tmp_path, monkeypatch):
    s3 = FakeS3()
    old_key = "map/20260101T010000Z/liberty.json"
    s3.objects[old_key] = {"body": b"partial"}

    def fail_delete(**kwargs):
        raise OSError("cleanup failed")

    monkeypatch.setattr(s3, "delete_objects", fail_delete)
    build_id = "20261122T010000Z"
    with pytest.raises(OSError, match="cleanup failed"):
        publish_map_styles(s3, "bucket", build_id, map_files(tmp_path), {"dataDate": "2026-11-22"})
    latest = json.loads(s3.objects[LATEST_KEY]["body"])
    assert latest["buildId"] == build_id
    assert latest["manifest"] in s3.objects
    assert s3.writes[-1] == LATEST_KEY
    assert old_key in s3.objects
