import hashlib

import pytest

requests = pytest.importorskip("requests")

from search.sources import ChecksumError, download


class FakeResponse:
    def __init__(self, body: bytes, status=200):
        self.body, self.status_code, self.text = body, status, body.decode("latin-1")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, size):
        for i in range(0, len(self.body), 3):
            yield self.body[i:i + 3]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, files):
        self.files, self.calls = files, []

    def get(self, url, **kw):
        self.calls.append(url)
        return FakeResponse(self.files[url])


def test_download_verifies_md5_and_reports_hashes(tmp_path):
    body = b"photon dump bytes"
    md5 = hashlib.md5(body).hexdigest()
    s = FakeSession({"https://x/a.zst": body, "https://x/a.zst.md5": f"{md5}  a.zst\n".encode()})
    info = download("https://x/a.zst", tmp_path / "a.zst", session=s)
    assert (tmp_path / "a.zst").read_bytes() == body
    assert info == {"url": "https://x/a.zst", "sha256": hashlib.sha256(body).hexdigest(), "md5": md5, "bytes": len(body)}


def test_download_rejects_a_md5_mismatch(tmp_path):
    s = FakeSession({"https://x/a.zst": b"abc", "https://x/a.zst.md5": b"0" * 32 + b"  a.zst"})
    with pytest.raises(ChecksumError):
        download("https://x/a.zst", tmp_path / "a.zst", session=s)


@pytest.mark.parametrize("md5_text", [b"", b"   \n", b"not-a-hash  a.zst", b"0" * 31 + b"  a.zst"])
def test_download_rejects_an_unreadable_md5_file(tmp_path, md5_text):
    s = FakeSession({"https://x/a.zst": b"abc", "https://x/a.zst.md5": md5_text})
    with pytest.raises(ChecksumError):
        download("https://x/a.zst", tmp_path / "a.zst", session=s)
    assert s.calls == ["https://x/a.zst.md5"]  # the large file is not fetched


def test_download_accepts_uppercase_checksum(tmp_path):
    body = b"abc"
    digest = hashlib.md5(body).hexdigest()
    session = FakeSession({"https://x/a": body, "https://x/a.md5": digest.upper().encode()})
    assert download("https://x/a", tmp_path / "a", session=session)["md5"] == digest


@pytest.mark.parametrize("failed_url", ["https://x/a.md5", "https://x/a"])
def test_http_errors_abort_without_overwriting_destination(tmp_path, failed_url):
    class Session(FakeSession):
        def get(self, url, **kw):
            self.calls.append(url)
            if url == failed_url:
                return FakeResponse(b"unavailable", status=503)
            return FakeResponse(hashlib.md5(b"abc").hexdigest().encode())

    dest = tmp_path / "a"
    dest.write_bytes(b"previous download")
    session = Session({})
    with pytest.raises(RuntimeError, match="HTTP 503"):
        download("https://x/a", dest, session=session)
    assert dest.read_bytes() == b"previous download"
    assert session.calls == (["https://x/a.md5"] if failed_url.endswith(".md5") else
                             ["https://x/a.md5", "https://x/a"])


def test_stream_failure_propagates_and_closes_response(tmp_path):
    class BrokenResponse(FakeResponse):
        closed = False

        def iter_content(self, size):
            yield b"partial"
            raise OSError("connection lost")

        def __exit__(self, *exc):
            self.closed = True
            return False

    response = BrokenResponse(b"")

    class Session:
        def get(self, url, **kw):
            if url.endswith(".md5"):
                return FakeResponse(b"0" * 32)
            return response

    with pytest.raises(OSError, match="connection lost"):
        download("https://x/a", tmp_path / "a", session=Session())
    assert response.closed


def _http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=response)


def test_download_retries_the_whole_download_after_transient_errors(tmp_path):
    body = b"abc"
    good = hashlib.md5(body).hexdigest().encode()

    class Flaky(FakeSession):
        failures = [requests.ConnectionError("reset"), _http_error(503)]

        def get(self, url, **kw):
            self.calls.append(url)
            if url.endswith(".md5"):
                return FakeResponse(good)
            if self.failures:
                raise self.failures.pop(0)
            return FakeResponse(body)

    waits, session = [], Flaky({})
    info = download("https://x/a", tmp_path / "a", session=session, attempts=3, sleep=waits.append)
    assert info["md5"] == good.decode()
    assert session.calls == ["https://x/a.md5", "https://x/a"] * 3  # the md5 is fetched again each time
    assert waits == [60, 300]


def test_download_retries_a_checksum_mismatch(tmp_path):
    # The publisher may replace the file between our md5 request and the download.
    body = b"new file"
    md5s = [b"0" * 32, hashlib.md5(body).hexdigest().encode()]

    class Replaced(FakeSession):
        def get(self, url, **kw):
            self.calls.append(url)
            return FakeResponse(md5s.pop(0) if url.endswith(".md5") else body)

    waits = []
    assert download("https://x/a", tmp_path / "a", session=Replaced({}), attempts=3, sleep=waits.append)["bytes"] == len(body)
    assert waits == [60]


@pytest.mark.parametrize("error", [_http_error(404), _http_error(403)])
def test_download_does_not_retry_a_permanent_http_error(tmp_path, error):
    class Missing(FakeSession):
        def get(self, url, **kw):
            self.calls.append(url)
            raise error

    waits, session = [], Missing({})
    with pytest.raises(requests.HTTPError):
        download("https://x/a", tmp_path / "a", session=session, attempts=3, sleep=waits.append)
    assert waits == [] and session.calls == ["https://x/a.md5"]


def test_download_gives_up_after_the_last_attempt(tmp_path):
    class Down(FakeSession):
        def get(self, url, **kw):
            self.calls.append(url)
            raise requests.Timeout("slow")

    waits, session = [], Down({})
    with pytest.raises(requests.Timeout):
        download("https://x/a", tmp_path / "a", session=session, attempts=3, sleep=waits.append)
    assert len(session.calls) == 3 and waits == [60, 300]
