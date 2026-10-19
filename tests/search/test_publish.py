import json

import pytest

from search.publish import LATEST_KEY, AlreadyPublished, previous_counts, prune, publish


class FakeS3:
    class exceptions:
        class ClientError(Exception):
            def __init__(self, code):
                self.response = {"Error": {"Code": code}}

    def __init__(self):
        self.objects, self.deleted = {}, []

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        self.objects[Key] = {"body": Body, "type": ContentType, "cache": CacheControl}

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


MANIFEST = {"buildId": "20261101T183000Z", "dataDate": "2026-10-31",
            "files": {"places": {"documents": 5}, "nearby": {"documents": 2}}}


def test_publish_uploads_the_build_then_latest(tmp_path):
    s3 = FakeS3()
    f = tmp_path / "places.ndjson.gz"
    f.write_bytes(b"gz")
    publish(s3, "bucket", "20261101T183000Z", {"places": f}, MANIFEST)
    assert s3.objects["search/20261101T183000Z/places.ndjson.gz"]["type"] == "application/gzip"
    assert json.loads(s3.objects["search/20261101T183000Z/manifest.json"]["body"]) == MANIFEST
    assert "search/20261101T183000Z/LICENSE.md" in s3.objects
    assert json.loads(s3.objects[LATEST_KEY]["body"]) == {
        "buildId": "20261101T183000Z", "dataDate": "2026-10-31", "manifest": "search/20261101T183000Z/manifest.json"}
    assert previous_counts(s3, "bucket") == {"places": 5, "nearby": 2}


@pytest.mark.parametrize("existing", ["manifest.json", "places.ndjson.gz"])
def test_a_build_folder_with_anything_in_it_is_never_overwritten(tmp_path, existing):
    # A partly uploaded build (no manifest yet) counts as published too.
    s3 = FakeS3()
    s3.objects[f"search/20261101T183000Z/{existing}"] = {"body": b"old"}
    f = tmp_path / "places.ndjson.gz"
    f.write_bytes(b"gz")
    with pytest.raises(AlreadyPublished):
        publish(s3, "bucket", "20261101T183000Z", {"places": f}, MANIFEST)
    assert s3.objects[f"search/20261101T183000Z/{existing}"]["body"] == b"old"
    assert LATEST_KEY not in s3.objects


def test_previous_counts_is_none_before_the_first_publish():
    assert previous_counts(FakeS3(), "bucket") is None


def test_prune_keeps_the_newest_three_builds():
    s3 = FakeS3()
    for b in ["20260801T183000Z", "20260901T183000Z", "20261001T183000Z", "20261101T183000Z"]:
        s3.objects[f"search/{b}/manifest.json"] = {"body": b"{}"}
    s3.objects[LATEST_KEY] = {"body": b"{}"}
    prune(s3, "bucket", keep=3)
    assert s3.deleted == ["search/20260801T183000Z/manifest.json"]
    assert LATEST_KEY in s3.objects


@pytest.mark.parametrize("failed_name", ["places.ndjson.gz", "LICENSE.md", "manifest.json", "latest.json"])
def test_failed_upload_keeps_previous_pointer_and_does_not_prune(tmp_path, failed_name):
    class FailingS3(FakeS3):
        def put_object(self, **kw):
            if kw["Key"].rsplit("/", 1)[-1] == failed_name:
                raise OSError("upload failed")
            super().put_object(**kw)

    s3 = FailingS3()
    old_latest = {"body": b'{"buildId":"20261001T183000Z"}'}
    s3.objects[LATEST_KEY] = old_latest.copy()
    for month in (7, 8, 9, 10):
        s3.objects[f"search/2026{month:02d}01T183000Z/manifest.json"] = {"body": b"{}"}
    f = tmp_path / "places.ndjson.gz"
    f.write_bytes(b"gz")
    with pytest.raises(OSError, match="upload failed"):
        publish(s3, "bucket", MANIFEST["buildId"], {"places": f}, MANIFEST)
    assert s3.objects[LATEST_KEY] == old_latest
    assert s3.deleted == []


def test_latest_is_written_only_after_all_build_objects(tmp_path):
    class RecordingS3(FakeS3):
        def __init__(self):
            super().__init__()
            self.writes = []

        def put_object(self, **kw):
            self.writes.append(kw["Key"])
            super().put_object(**kw)

    s3 = RecordingS3()
    files = {}
    for name in ("places.ndjson.gz", "nearby.ndjson.gz", "probes.json"):
        path = tmp_path / name
        path.write_bytes(b"fixture")
        files[name] = path
    publish(s3, "bucket", MANIFEST["buildId"], files, MANIFEST)
    prefix = f"search/{MANIFEST['buildId']}/"
    assert s3.writes == [prefix + name for name in
                         ("places.ndjson.gz", "nearby.ndjson.gz", "probes.json", "LICENSE.md", "manifest.json")] + [LATEST_KEY]
    assert s3.objects[LATEST_KEY]["cache"] == "public, max-age=300"
    assert all(s3.objects[key]["cache"] == "public, max-age=86400, immutable" for key in s3.writes[:-1])


def test_partial_builds_do_not_evict_the_previous_complete_build(tmp_path):
    # Regression: currently fails because prune counts partial folders toward KEEP_BUILDS.
    # A reader may still hold the previous latest.json (cached for five minutes).
    s3 = FakeS3()
    old_id = "20261001T183000Z"
    old_manifest = f"search/{old_id}/manifest.json"
    files = {}
    for name in ("places.ndjson.gz", "nearby.ndjson.gz", "probes.json"):
        path = tmp_path / name
        path.write_bytes(b"fixture")
        files[name] = path
    publish(s3, "bucket", old_id, files, {**MANIFEST, "buildId": old_id})
    for partial_id in ("20261002T183000Z", "20261003T183000Z"):
        s3.objects[f"search/{partial_id}/places.ndjson.gz"] = {"body": b"partial"}
    publish(s3, "bucket", MANIFEST["buildId"], files, MANIFEST)
    latest = json.loads(s3.objects[LATEST_KEY]["body"])
    assert latest["manifest"] in s3.objects
    assert old_manifest in s3.objects


def test_prune_deletes_partial_folders_and_never_the_build_latest_names():
    s3 = FakeS3()
    complete = ["20260801T183000Z", "20260901T183000Z", "20261001T183000Z", "20261101T183000Z"]
    for b in complete:
        s3.objects[f"search/{b}/manifest.json"] = {"body": b"{}"}
    s3.objects["search/20261015T183000Z/places.ndjson.gz"] = {"body": b"partial"}
    # latest.json still names the oldest build, as after a rollback of the published pointer.
    s3.objects[LATEST_KEY] = {"body": json.dumps({"buildId": "20260801T183000Z"}).encode()}
    prune(s3, "bucket", keep=3)
    assert sorted(s3.deleted) == ["search/20261015T183000Z/places.ndjson.gz"]
    assert "search/20260801T183000Z/manifest.json" in s3.objects


def test_publish_keeps_the_build_it_replaced_even_behind_unpublished_complete_builds(tmp_path):
    # A is live; runs B and C uploaded a manifest but failed writing latest.json; D publishes.
    # Readers may still hold the cached pointer to A for five minutes, so A must survive D's prune.
    s3 = FakeS3()
    f = tmp_path / "places.ndjson.gz"
    f.write_bytes(b"gz")
    a, b, c, d, e = ("20260801T183000Z", "20260901T183000Z", "20261001T183000Z",
                     "20261101T183000Z", "20261201T183000Z")
    publish(s3, "bucket", a, {"places": f}, {**MANIFEST, "buildId": a})
    for unpublished in (b, c):
        s3.objects[f"search/{unpublished}/manifest.json"] = {"body": b"{}"}
    publish(s3, "bucket", d, {"places": f}, {**MANIFEST, "buildId": d})
    assert f"search/{a}/manifest.json" in s3.objects
    # The next publish replaces D; A is two pointers back by then and goes, with B.
    publish(s3, "bucket", e, {"places": f}, {**MANIFEST, "buildId": e})
    assert f"search/{a}/manifest.json" not in s3.objects
    assert f"search/{b}/manifest.json" not in s3.objects
    assert all(f"search/{x}/manifest.json" in s3.objects for x in (c, d, e))
