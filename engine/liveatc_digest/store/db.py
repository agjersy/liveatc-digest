"""SQLite store with file-based migrations and an append-only audit log."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

SCHEMA_VERSION_TABLE = "schema_migrations"


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class FetchRecord:
    id: int
    provider: str
    url: str
    sha256: str
    bytes: int
    content_type: str | None
    fetched_at: str
    licence: str | None


class Store:
    def __init__(self, path: Path, actor: str = "cli") -> None:
        self.path = path
        self.actor = actor
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # -- schema -------------------------------------------------------------------------

    def migrate(self) -> list[str]:
        self.conn.execute(
            f"CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} "
            "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {
            row["name"] for row in self.conn.execute(f"SELECT name FROM {SCHEMA_VERSION_TABLE}")
        }
        newly: list[str] = []
        migration_dir = resources.files("liveatc_digest.store") / "migrations"
        names = sorted(p.name for p in migration_dir.iterdir() if p.name.endswith(".sql"))
        for name in names:
            if name in applied:
                continue
            sql = (migration_dir / name).read_text(encoding="utf-8")
            with self.conn:
                self.conn.executescript(sql)
                self.conn.execute(
                    f"INSERT INTO {SCHEMA_VERSION_TABLE} (name, applied_at) VALUES (?, ?)",
                    (name, utcnow_iso()),
                )
            newly.append(name)
        return newly

    def schema_version(self) -> str | None:
        row = self.conn.execute(
            f"SELECT name FROM {SCHEMA_VERSION_TABLE} ORDER BY name DESC LIMIT 1"
        ).fetchone()
        return row["name"] if row else None

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    def close(self) -> None:
        self.conn.close()

    # -- audit --------------------------------------------------------------------------

    def audit(self, action: str, target: str | None = None, **details: Any) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO audit_log (utc, actor, action, target, details_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (utcnow_iso(), self.actor, action, target, json.dumps(details) or None),
            )
        return int(cur.lastrowid)

    # -- provenance ---------------------------------------------------------------------

    def record_fetch(
        self,
        provider: str,
        url: str,
        sha256: str,
        size: int,
        content_type: str | None = None,
        licence: str | None = None,
    ) -> FetchRecord:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO source_fetch "
                "(provider, url, sha256, bytes, content_type, fetched_at, licence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (provider, url, sha256, size, content_type, utcnow_iso(), licence),
            )
        rec = self.find_fetch(url, provider)
        assert rec is not None
        return rec

    def find_fetch(self, url: str, provider: str | None = None) -> FetchRecord | None:
        sql = "SELECT * FROM source_fetch WHERE url = ?"
        params: list[Any] = [url]
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        sql += " ORDER BY fetched_at DESC LIMIT 1"
        row = self.conn.execute(sql, params).fetchone()
        return FetchRecord(**dict(row)) if row else None

    # -- stations & blocks ----------------------------------------------------------------

    def upsert_station(
        self,
        icao: str,
        identifier: str,
        title: str,
        role: str,
        up: bool,
        frequencies: list[dict[str, str]],
        archive_id: str | None = None,
    ) -> int:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO station (icao, identifier, title, role, up, frequencies_json,
                                     archive_id, seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identifier) DO UPDATE SET
                    icao = excluded.icao, title = excluded.title, role = excluded.role,
                    up = excluded.up, frequencies_json = excluded.frequencies_json,
                    archive_id = COALESCE(excluded.archive_id, station.archive_id),
                    seen_at = excluded.seen_at
                """,
                (
                    icao.upper(),
                    identifier,
                    title,
                    role,
                    int(up),
                    json.dumps(frequencies),
                    archive_id,
                    utcnow_iso(),
                ),
            )
        row = self.conn.execute(
            "SELECT id FROM station WHERE identifier = ?", (identifier,)
        ).fetchone()
        return int(row["id"])

    def get_station(self, identifier: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM station WHERE identifier = ?", (identifier,)
        ).fetchone()

    def set_station_archive_id(self, identifier: str, archive_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE station SET archive_id = ? WHERE identifier = ?",
                (archive_id, identifier),
            )

    def upsert_audio_block(
        self,
        station_id: int,
        icao: str,
        start_utc: str,
        end_utc: str,
        status: str,
        source_fetch_id: int | None = None,
    ) -> int:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO audio_block (station_id, icao, start_utc, end_utc,
                                         source_fetch_id, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(station_id, start_utc) DO UPDATE SET
                    end_utc = excluded.end_utc,
                    source_fetch_id = COALESCE(excluded.source_fetch_id,
                                               audio_block.source_fetch_id),
                    status = excluded.status
                """,
                (station_id, icao.upper(), start_utc, end_utc, source_fetch_id, status),
            )
        row = self.conn.execute(
            "SELECT id FROM audio_block WHERE station_id = ? AND start_utc = ?",
            (station_id, start_utc),
        ).fetchone()
        return int(row["id"])

    def counts(self) -> dict[str, int]:
        tables = ["source_fetch", "station", "audio_block", "utterance", "job", "audit_log"]
        return {
            t: int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"])
            for t in tables
        }
