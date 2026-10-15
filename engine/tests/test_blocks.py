from datetime import UTC, datetime, timedelta

import pytest

from liveatc_digest.sources.liveatc import (
    Block,
    BlockStatus,
    archive_filename,
    archive_url,
    block_status,
    plan_blocks,
)
from liveatc_digest.sources.liveatc.archive import parse_archive_id


def test_plan_blocks_aligns_to_half_hours():
    start = datetime(2024, 7, 28, 0, 10, tzinfo=UTC)
    end = datetime(2024, 7, 28, 1, 5, tzinfo=UTC)
    blocks = plan_blocks(start, end)
    assert [b.start_utc.strftime("%H%M") for b in blocks] == ["0000", "0030", "0100"]
    assert blocks[0].end_utc == datetime(2024, 7, 28, 0, 30, tzinfo=UTC)


def test_plan_blocks_naive_is_utc_and_exclusive_end():
    blocks = plan_blocks(datetime(2024, 7, 28, 0, 0), datetime(2024, 7, 28, 1, 0))
    assert len(blocks) == 2
    assert blocks[0].start_utc.tzinfo is UTC


def test_plan_blocks_rejects_empty_window():
    with pytest.raises(ValueError):
        plan_blocks(datetime(2024, 7, 28, 1, 0), datetime(2024, 7, 28, 1, 0))


def test_filename_and_url_match_liveatc_layout():
    block = Block(datetime(2024, 7, 28, 0, 0, tzinfo=UTC))
    assert archive_filename("KBED-Twr", block) == "KBED-Twr-Jul-28-2024-0000Z.mp3"
    assert archive_url("KBED", "KBED-Twr", block) == (
        "https://archive.liveatc.net/kbed/KBED-Twr-Jul-28-2024-0000Z.mp3"
    )


def test_block_status_retention():
    now = datetime(2024, 8, 30, 12, 0, tzinfo=UTC)
    assert block_status(Block(now - timedelta(days=1)), now) is BlockStatus.AVAILABLE
    assert block_status(Block(now - timedelta(days=31)), now) is BlockStatus.EXPIRED
    assert block_status(Block(now + timedelta(hours=1)), now) is BlockStatus.FUTURE


def test_parse_archive_id(archive_html):
    assert parse_archive_id(archive_html) == "KBED-Twr"


def test_parse_archive_id_rejects_missing():
    with pytest.raises(ValueError):
        parse_archive_id("<html><select><option value='x'>x</option></select></html>")
