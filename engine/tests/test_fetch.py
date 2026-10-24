from datetime import UTC, datetime

import pytest

from liveatc_digest.http import HttpClient
from liveatc_digest.sources.liveatc import ArchiveNotAvailable, Block, fetch_block
from tests.conftest import FakeResponse

MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 100
URL = "https://archive.liveatc.net/kbed/KBED-Twr-Jul-28-2024-0000Z.mp3"
BLOCK = Block(datetime(2024, 7, 28, 0, 0, tzinfo=UTC))
NOW = datetime(2024, 7, 29, 0, 0, tzinfo=UTC)


def test_fetch_block_downloads_then_hits_cache(client, session, cache, store):
    session.add(URL, FakeResponse(200, MP3, "audio/mpeg"))
    first = fetch_block(client, cache, store, "KBED", "KBED-Twr", BLOCK, now=NOW)
    assert first.cached is False
    assert first.path.read_bytes() == MP3
    rec = store.find_fetch(URL, "liveatc")
    assert rec is not None and rec.sha256 == first.sha256

    second = fetch_block(client, cache, store, "KBED", "KBED-Twr", BLOCK, now=NOW)
    assert second.cached is True
    assert session.calls.count(URL) == 1


def test_fetch_block_404(client, session, cache, store):
    with pytest.raises(ArchiveNotAvailable) as exc:
        fetch_block(client, cache, store, "KBED", "KBED-Twr", BLOCK, now=NOW)
    assert "HTTP 404" in exc.value.reason
    assert store.find_fetch(URL) is None


def test_fetch_block_rejects_html_body(client, session, cache, store):
    session.add(URL, FakeResponse(200, b"<html>feed down</html>", "text/html"))
    with pytest.raises(ArchiveNotAvailable):
        fetch_block(client, cache, store, "KBED", "KBED-Twr", BLOCK, now=NOW)
    assert cache.stats() == (0, 0)


def test_fetch_block_expired_does_not_hit_network(client, session, cache, store):
    late = datetime(2024, 9, 15, tzinfo=UTC)
    with pytest.raises(ArchiveNotAvailable) as exc:
        fetch_block(client, cache, store, "KBED", "KBED-Twr", BLOCK, now=late)
    assert "archive" in exc.value.reason
    assert session.calls == []


def test_http_retries_on_connection_error(session, network_error):
    session.add(URL, [network_error, FakeResponse(200, b"ok", "text/plain")])
    client = HttpClient("t", retries=2, session=session, sleep=lambda s: None)
    assert client.get_text(URL) == "ok"
    assert session.calls.count(URL) == 2


def test_challenge_page_is_reported_distinctly(session):
    from liveatc_digest.http import ChallengeError

    body = b"<html><title>Just a moment...</title><div id='cf-chl'></div></html>"
    session.add("https://www.liveatc.net/search/?icao=kbed", FakeResponse(403, body, "text/html"))
    client = HttpClient("t", session=session, sleep=lambda s: None)
    with pytest.raises(ChallengeError):
        client.get_text("https://www.liveatc.net/search/?icao=kbed")


def test_plain_403_is_a_normal_http_error(session):
    from liveatc_digest.http import ChallengeError, HttpError

    session.add("https://example.test/x", FakeResponse(403, b"forbidden", "text/plain"))
    client = HttpClient("t", session=session, sleep=lambda s: None)
    with pytest.raises(HttpError) as exc:
        client.get_text("https://example.test/x")
    assert not isinstance(exc.value, ChallengeError)
