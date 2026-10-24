"""Small HTTP client with retries and streaming downloads.

Tests inject a fake ``requests.Session``-like object, so everything goes through
``HttpClient`` rather than module-level ``requests`` calls.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import requests

RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str = "") -> None:
        self.url = url
        self.status = status
        super().__init__(message or f"{url} -> {status}")


class ChallengeError(HttpError):
    """The site answered with a bot-protection (human verification) page instead of content.

    We do not try to get past these. Callers surface the alternatives: register the station
    manually or import a page the person opened in their own browser.
    """

    def __init__(self, url: str, status: int) -> None:
        super().__init__(url, status, f"{url} -> {status} (human-verification page)")


_CHALLENGE_MARKERS = ("just a moment", "cf-chl", "challenge-platform", "verify you are human")


def looks_like_challenge(status: int, headers, body: str) -> bool:
    if str(headers.get("cf-mitigated", "")).lower() == "challenge":
        return True
    if status in (403, 429, 503):
        head = (body or "")[:4000].lower()
        return any(m in head for m in _CHALLENGE_MARKERS)
    return False


@dataclass
class DownloadResult:
    path: Path
    content_type: str
    size: int


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        timeout_s: float = 60.0,
        retries: int = 3,
        backoff_s: float = 1.0,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self._sleep = sleep

    def _request(self, method: str, url: str, **kwargs):
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.request(method, url, timeout=self.timeout_s, **kwargs)
            except requests.RequestException as exc:  # network-level failure
                last_exc = exc
            else:
                if resp.status_code in RETRY_STATUSES and attempt < self.retries:
                    last_exc = HttpError(url, resp.status_code)
                else:
                    return resp
            if attempt < self.retries:
                self._sleep(self.backoff_s * (2**attempt))
        raise HttpError(url, None, f"request failed after retries: {last_exc}")

    def get_text(self, url: str) -> str:
        resp = self._request("GET", url)
        if looks_like_challenge(resp.status_code, resp.headers, resp.text):
            raise ChallengeError(url, resp.status_code)
        if resp.status_code != 200:
            raise HttpError(url, resp.status_code)
        return resp.text

    def download(self, url: str, dest: Path, chunk_size: int = 1 << 16) -> DownloadResult:
        resp = self._request("GET", url, stream=True)
        if resp.status_code != 200:
            if looks_like_challenge(resp.status_code, resp.headers, ""):
                raise ChallengeError(url, resp.status_code)
            raise HttpError(url, resp.status_code)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        size = 0
        with tmp.open("wb") as fh:
            chunks: Iterable[bytes] = resp.iter_content(chunk_size=chunk_size)
            for chunk in chunks:
                if chunk:
                    fh.write(chunk)
                    size += len(chunk)
        tmp.replace(dest)
        return DownloadResult(dest, resp.headers.get("Content-Type", ""), size)
