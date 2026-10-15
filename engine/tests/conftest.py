from __future__ import annotations

from pathlib import Path

import pytest
import requests

from liveatc_digest.cache import Cache
from liveatc_digest.http import HttpClient
from liveatc_digest.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", content_type: str = "text/html") -> None:
        self.status_code = status
        self._body = body
        self.headers = {"Content-Type": content_type}
        self.text = body.decode("utf-8", errors="replace")

    def iter_content(self, chunk_size: int = 65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]


class FakeSession:
    """Minimal stand-in for requests.Session keyed by URL."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.routes: dict[str, FakeResponse | list[FakeResponse] | Exception] = {}
        self.calls: list[str] = []

    def add(self, url: str, response) -> None:
        self.routes[url] = response

    def request(self, method: str, url: str, **kwargs):
        self.calls.append(url)
        route = self.routes.get(url)
        if route is None:
            return FakeResponse(404, b"not found")
        if isinstance(route, Exception):
            raise route
        if isinstance(route, list):
            resp = route.pop(0) if len(route) > 1 else route[0]
            if isinstance(resp, Exception):
                raise resp
            return resp
        return route


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("LIVEATC_DIGEST_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def client(session) -> HttpClient:
    return HttpClient("test-agent", session=session, sleep=lambda s: None)


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "cache")


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store(tmp_path / "ws.sqlite", actor="test")
    yield s
    s.close()


@pytest.fixture
def search_html() -> str:
    return (FIXTURES / "liveatc_search_kbed.html").read_text()


@pytest.fixture
def archive_html() -> str:
    return (FIXTURES / "liveatc_archive_kbed_twr.html").read_text()


@pytest.fixture
def network_error() -> Exception:
    return requests.ConnectionError("boom")
