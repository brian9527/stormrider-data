import hashlib
import json
from pathlib import Path

import pytest

import mapstyle.main as main_mod
from mapstyle.checks import MAX_BYTES, StyleCheckFailed
from mapstyle.main import build, build_manifest, content_hash, fetch_style
from mapstyle.publish import LATEST_KEY

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
    b["fiord"]["body"] = b["fiord"]["body"] + b" "
    assert content_hash(a) != content_hash(b)


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
