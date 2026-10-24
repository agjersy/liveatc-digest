"""LiveATC archive blocks: planning, URL construction, retention, and cached download.

LiveATC stores each feed as 30-minute MP3 blocks aligned to :00 and :30 UTC at
``https://archive.liveatc.net/<icao>/<ARCHIVE-ID>-<Mon-DD-YYYY>-<HHMM>Z.mp3`` and keeps them
for roughly 30 days.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from bs4 import BeautifulSoup

from ...cache import Cache
from ...http import ChallengeError, HttpClient, HttpError
from ...store import Store

PROVIDER = "liveatc"
LICENCE = "LiveATC.net terms: personal, non-commercial use; audio is not redistributed."
BLOCK_MINUTES = 30
ARCHIVE_PAGE_URL = "https://www.liveatc.net/archive.php?m={mount}"
ARCHIVE_BASE_URL = "https://archive.liveatc.net/{icao}/{filename}"
_MP3_MAGIC = (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xe3")


class ArchiveNotAvailable(RuntimeError):
    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"{reason}: {url}")


class BlockStatus(StrEnum):
    AVAILABLE = "available"  # within the retention window; may still 404 if the feed was down
    EXPIRED = "expired"
    FUTURE = "future"


@dataclass(frozen=True)
class Block:
    start_utc: datetime

    @property
    def end_utc(self) -> datetime:
        return self.start_utc + timedelta(minutes=BLOCK_MINUTES)


@dataclass(frozen=True)
class FetchedBlock:
    block: Block
    path: Path
    sha256: str
    url: str
    cached: bool


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def floor_to_block(dt: datetime) -> datetime:
    dt = ensure_utc(dt)
    minute = (dt.minute // BLOCK_MINUTES) * BLOCK_MINUTES
    return dt.replace(minute=minute, second=0, microsecond=0)


def plan_blocks(start: datetime, end: datetime) -> list[Block]:
    """All blocks that overlap the half-open interval [start, end)."""
    start, end = ensure_utc(start), ensure_utc(end)
    if end <= start:
        raise ValueError("end must be after start")
    cursor = floor_to_block(start)
    blocks: list[Block] = []
    while cursor < end:
        blocks.append(Block(cursor))
        cursor += timedelta(minutes=BLOCK_MINUTES)
    return blocks


def archive_filename(archive_id: str, block: Block) -> str:
    start = block.start_utc
    return f"{archive_id}-{start:%b-%d-%Y}-{start:%H%M}Z.mp3"


def archive_url(icao: str, archive_id: str, block: Block) -> str:
    return ARCHIVE_BASE_URL.format(icao=icao.lower(), filename=archive_filename(archive_id, block))


def block_status(
    block: Block, now: datetime | None = None, retention_days: int = 30
) -> BlockStatus:
    now = ensure_utc(now or datetime.now(UTC))
    if block.start_utc > now:
        return BlockStatus.FUTURE
    if block.end_utc < now - timedelta(days=retention_days):
        return BlockStatus.EXPIRED
    return BlockStatus.AVAILABLE


_ARCHIVE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]*$")
_FILENAME_RE = re.compile(
    r"^(?P<archive_id>[A-Za-z0-9][A-Za-z0-9\-]*?)-"
    r"(?P<mon>[A-Z][a-z]{2})-(?P<day>\d{2})-(?P<year>\d{4})-(?P<hm>\d{4})Z\.mp3$"
)


def parse_archive_filename(name: str) -> tuple[str, Block]:
    """``KBED-Twr-Oct-24-2026-0000Z.mp3`` -> (``KBED-Twr``, Block(2026-10-24T00:00Z))."""
    m = _FILENAME_RE.match(Path(name).name)
    if not m:
        raise ValueError(f"not a LiveATC archive filename: {name!r}")
    start = datetime.strptime(
        f"{m['mon']}-{m['day']}-{m['year']}-{m['hm']}", "%b-%d-%Y-%H%M"
    ).replace(tzinfo=UTC)
    return m["archive_id"], Block(start)


def parse_archive_id(html: str) -> str:
    """Read the selected ``<option>`` of the archive page's archive-identifier ``<select>``."""
    soup = BeautifulSoup(html, "html.parser")
    option = soup.find("option", selected=True)
    if option is None or not option.get("value"):
        raise ValueError("archive page has no selected archive identifier")
    value = str(option["value"]).strip()
    if not _ARCHIVE_ID_RE.match(value):
        raise ValueError(f"unexpected archive identifier: {value!r}")
    return value


def resolve_archive_id(client: HttpClient, mount: str) -> str:
    return parse_archive_id(client.get_text(ARCHIVE_PAGE_URL.format(mount=mount)))


def _looks_like_mp3(path: Path, content_type: str) -> bool:
    if "audio" in content_type.lower():
        return True
    with path.open("rb") as fh:
        head = fh.read(4)
    return any(head.startswith(m) for m in _MP3_MAGIC)


def fetch_block(
    client: HttpClient,
    cache: Cache,
    store: Store,
    icao: str,
    archive_id: str,
    block: Block,
    retention_days: int = 30,
    now: datetime | None = None,
) -> FetchedBlock:
    """Download one archive block (or return the cached copy) and record provenance."""
    url = archive_url(icao, archive_id, block)

    existing = store.find_fetch(url, PROVIDER)
    if existing and cache.has(existing.sha256):
        return FetchedBlock(block, cache.path_for(existing.sha256), existing.sha256, url, True)

    status = block_status(block, now, retention_days)
    if status is BlockStatus.EXPIRED:
        raise ArchiveNotAvailable(
            url, f"block is outside the ~{retention_days}-day LiveATC archive"
        )
    if status is BlockStatus.FUTURE:
        raise ArchiveNotAvailable(url, "block is in the future")

    tmp = cache.temp_path(".mp3")
    try:
        result = client.download(url, tmp)
    except ChallengeError as exc:
        raise ArchiveNotAvailable(
            url,
            "the archive host answered with a human-verification page; download the file in "
            "your browser and use `latc import`",
        ) from exc
    except HttpError as exc:
        if exc.status == 403:
            reason = (
                "HTTP 403 (refused by the archive host; run `latc diagnose <url>` to see why, "
                "or download it in your browser and use `latc import`)"
            )
        elif exc.status is not None:
            reason = f"HTTP {exc.status}"
        else:
            reason = f"network error: {exc}"
        raise ArchiveNotAvailable(url, reason) from exc
    if result.size == 0 or not _looks_like_mp3(tmp, result.content_type):
        tmp.unlink(missing_ok=True)
        raise ArchiveNotAvailable(url, "response was not an MP3 (feed may have been down)")

    obj = cache.put_file(tmp)
    store.record_fetch(PROVIDER, url, obj.sha256, obj.size, result.content_type, LICENCE)
    return FetchedBlock(block, obj.path, obj.sha256, url, False)


def import_block(
    cache: Cache,
    store: Store,
    icao: str,
    archive_id: str,
    block: Block,
    source_path: Path,
) -> FetchedBlock:
    """Register a recording the person downloaded themselves as if it had been fetched.

    The file is copied into the cache and recorded against the archive URL it corresponds to,
    so later ``fetch_block`` calls are cache hits and never touch the network.
    """
    url = archive_url(icao, archive_id, block)
    if not source_path.exists() or source_path.stat().st_size == 0:
        raise ArchiveNotAvailable(url, f"file missing or empty: {source_path}")
    if not _looks_like_mp3(source_path, ""):
        raise ArchiveNotAvailable(url, f"not an MP3 file: {source_path}")
    obj = cache.put_file(source_path, move=False)
    store.record_fetch(
        PROVIDER,
        url,
        obj.sha256,
        obj.size,
        "audio/mpeg",
        LICENCE + " Imported from a file the user downloaded in their browser.",
    )
    return FetchedBlock(block, obj.path, obj.sha256, url, True)
