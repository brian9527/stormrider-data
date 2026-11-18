import json

import pytest

from search.publish import LATEST_KEY, AlreadyPublished, previous_counts, prune, publish
from routing.publish import previous_tiles_bytes, publish_routing


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


def test_publish_and_prune_work_under_another_root(tmp_path):
    s3 = FakeS3()
    f = tmp_path / "tiles.tar"
    f.write_bytes(b"tar")
    manifest = {"buildId": "20261101T183000Z", "dataDate": "2026-10-31"}
    publish(s3, "bucket", "20261101T183000Z", {"tiles": f}, manifest,
            root="routing/", latest_key="routing/latest.json", license_md="# routing\n")
    assert s3.objects["routing/20261101T183000Z/tiles.tar"]["type"] == "application/x-tar"
    assert s3.objects["routing/20261101T183000Z/LICENSE.md"]["body"] == b"# routing\n"
    latest = json.loads(s3.objects["routing/latest.json"]["body"])
    assert latest["manifest"] == "routing/20261101T183000Z/manifest.json"
    assert not any(k.startswith("search/") for k in s3.objects)


def test_publish_keeps_the_number_of_builds_it_is_given(tmp_path):
    s3 = FakeS3()
    f = tmp_path / "tiles.tar"
    f.write_bytes(b"tar")
    for build in ("20261101T183000Z", "20261201T183000Z", "20270101T183000Z"):
        publish(s3, "bucket", build, {"tiles": f}, {"buildId": build, "dataDate": "2026-10-31"},
                root="routing/", latest_key="routing/latest.json", license_md="x", keep=2)
    folders = {k.split("/")[1] for k in s3.objects if k.startswith("routing/2")}
    # keep=2 plus the build latest.json named before the last publish
    assert folders == {"20261201T183000Z", "20270101T183000Z"}


def test_routing_wrapper_keeps_six_builds_without_touching_search(tmp_path):
    s3 = FakeS3()
    search_objects = {"search/latest.json": {"body": b'{"buildId":"20260101T183000Z"}'},
                      "search/20260101T183000Z/manifest.json": {"body": b"{}"}}
    s3.objects.update(search_objects)
    tiles = tmp_path / "tiles.tar"
    tiles.write_bytes(b"tar")
    assert previous_tiles_bytes(s3, "bucket") is None
    for month in range(1, 8):
        build = f"2026{month:02d}01T183000Z"
        manifest = {"buildId": build, "dataDate": f"2026-{month:02d}-01",
                    "files": {"tiles": {"bytes": 3}}}
        publish_routing(s3, "bucket", build, {"tiles": tiles}, manifest)
    assert {k.split("/")[1] for k in s3.objects if k.startswith("routing/2")} == {
        f"2026{month:02d}01T183000Z" for month in range(2, 8)}
    assert {key: s3.objects[key] for key in search_objects} == search_objects
    assert previous_tiles_bytes(s3, "bucket") == 3
    assert s3.objects["routing/20260701T183000Z/LICENSE.md"]["body"].startswith(b"# StormRider routing graph")


@pytest.mark.parametrize("failed_name", ["tiles.tar", "checks.json", "manifest.json", "latest.json"])
def test_routing_upload_failure_preserves_the_previous_pointer(tmp_path, failed_name):
    class FailingS3(FakeS3):
        def put_object(self, **kw):
            if kw["Key"].rsplit("/", 1)[-1] == failed_name:
                raise OSError("upload failed")
            super().put_object(**kw)

    s3 = FailingS3()
    previous = {"body": b'{"buildId":"20261001T183000Z"}'}
    s3.objects["routing/latest.json"] = previous.copy()
    files = {}
    for name in ("tiles.tar", "checks.json"):
        path = tmp_path / name
        path.write_bytes(b"fixture")
        files[name] = path
    with pytest.raises(OSError, match="upload failed"):
        publish_routing(s3, "bucket", MANIFEST["buildId"], files, MANIFEST)
    assert s3.objects["routing/latest.json"] == previous
    assert s3.deleted == []
