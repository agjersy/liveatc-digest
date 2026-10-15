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


# ---------------------------------------------------------------------------------------
# Phase 1: utterances, extraction results, reports
# ---------------------------------------------------------------------------------------


class _Phase1Mixin:
    conn: sqlite3.Connection

    def utterance_count_for_block(
        self, audio_block_id: int, asr_provider: str, asr_model: str
    ) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM utterance WHERE audio_block_id = ? AND asr_provider = ? "
            "AND asr_model = ?",
            (audio_block_id, asr_provider, asr_model),
        ).fetchone()
        return int(row["n"])

    def delete_utterances_for_block(self, audio_block_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM utterance WHERE audio_block_id = ?", (audio_block_id,))

    def insert_utterance(
        self,
        audio_block_id: int,
        start_utc: str,
        end_utc: str,
        text: str,
        normalized_text: str | None,
        asr_provider: str,
        asr_model: str,
        confidence: float | None,
        phase: str | None,
        feed_role: str | None,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO utterance (audio_block_id, start_utc, end_utc, text, normalized_text,"
                " asr_provider, asr_model, confidence, phase, feed_role)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    audio_block_id,
                    start_utc,
                    end_utc,
                    text,
                    normalized_text,
                    asr_provider,
                    asr_model,
                    confidence,
                    phase,
                    feed_role,
                ),
            )
        return int(cur.lastrowid)

    def set_utterance_phase(self, utterance_id: int, phase: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE utterance SET phase = ? WHERE id = ?", (phase, utterance_id))

    def replace_extractions(
        self, utterance_id: int, callsigns: list[Any], events: list[Any]
    ) -> list[int]:
        """Replace rule-based extractions for an utterance; returns the new event ids."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM callsign_mention WHERE utterance_id = ?", (utterance_id,)
            )
            self.conn.execute(
                "DELETE FROM atc_event WHERE utterance_id = ? AND source = 'rules'", (utterance_id,)
            )
            for c in callsigns:
                self.conn.execute(
                    "INSERT INTO callsign_mention (utterance_id, raw, callsign, kind, span_start,"
                    " span_end, confidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (utterance_id, c.raw, c.callsign, c.kind, c.span[0], c.span[1], c.confidence),
                )
            ids: list[int] = []
            for e in events:
                cur = self.conn.execute(
                    "INSERT INTO atc_event (utterance_id, type, fields_json, text, span_start,"
                    " span_end, confidence, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'rules')",
                    (
                        utterance_id,
                        e.type,
                        json.dumps(e.fields),
                        e.text,
                        e.span[0],
                        e.span[1],
                        e.confidence,
                    ),
                )
                ids.append(int(cur.lastrowid))
        return ids

    def utterances_in_window(
        self, icao: str, start_utc: str, end_utc: str, stations: list[str] | None = None
    ) -> list[sqlite3.Row]:
        """Utterances whose start lies in [start_utc, end_utc).

        Utterance timestamps are stored with millisecond precision (``...SS.mmmZ``) and are
        compared as strings, so callers must pass bounds in the same format (``iso_ms``).
        """
        sql = (
            "SELECT u.*, s.identifier AS feed, s.role AS station_role"
            " FROM utterance u"
            " JOIN audio_block b ON b.id = u.audio_block_id"
            " JOIN station s ON s.id = b.station_id"
            " WHERE b.icao = ? AND u.start_utc >= ? AND u.start_utc < ?"
        )
        params: list[Any] = [icao.upper(), start_utc, end_utc]
        if stations:
            placeholders = ",".join("?" * len(stations))
            sql += f" AND s.identifier IN ({placeholders})"
            params.extend(stations)
        sql += " ORDER BY u.start_utc, u.id"
        return self.conn.execute(sql, params).fetchall()

    def callsigns_for(self, utterance_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT callsign FROM callsign_mention WHERE utterance_id = ?"
            " AND kind != 'partial' ORDER BY callsign",
            (utterance_id,),
        ).fetchall()
        return [r["callsign"] for r in rows]

    def events_for(self, utterance_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, type, fields_json, text FROM atc_event WHERE utterance_id = ?"
            " ORDER BY span_start",
            (utterance_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "type": r["type"],
                "fields": json.loads(r["fields_json"]),
                "text": r["text"],
            }
            for r in rows
        ]

    def create_reconstruction(
        self,
        kind: str,
        icao: str | None,
        start_utc: str | None,
        end_utc: str | None,
        params: dict[str, Any],
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO reconstruction"
                " (kind, icao, start_utc, end_utc, params_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (kind, icao, start_utc, end_utc, json.dumps(params, sort_keys=True), utcnow_iso()),
            )
        return int(cur.lastrowid)

    def add_report_version(
        self,
        reconstruction_id: int,
        body_md: str,
        evidence_map: dict[str, Any],
        inputs_hash: str,
        validator_log: list[dict[str, str]],
        llm_provider: str | None = None,
        llm_model: str | None = None,
        prompt_hash: str | None = None,
    ) -> tuple[int, int]:
        with self.conn:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM report_version"
                " WHERE reconstruction_id = ?",
                (reconstruction_id,),
            ).fetchone()
            version = int(row["v"]) + 1
            cur = self.conn.execute(
                "INSERT INTO report_version (reconstruction_id, version, body_md,"
                " evidence_map_json, llm_provider, llm_model, prompt_hash, inputs_hash,"
                " validator_log_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    reconstruction_id,
                    version,
                    body_md,
                    json.dumps(evidence_map),
                    llm_provider,
                    llm_model,
                    prompt_hash,
                    inputs_hash,
                    json.dumps(validator_log),
                    utcnow_iso(),
                ),
            )
        return int(cur.lastrowid), version

    def list_reports(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT r.id AS reconstruction_id, r.kind, r.icao, r.start_utc, r.end_utc,"
            " r.created_at,"
            " MAX(v.version) AS versions"
            " FROM reconstruction r LEFT JOIN report_version v ON v.reconstruction_id = r.id"
            " GROUP BY r.id ORDER BY r.id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def get_report(self, reconstruction_id: int, version: int | None = None) -> sqlite3.Row | None:
        if version is None:
            return self.conn.execute(
                "SELECT * FROM report_version WHERE reconstruction_id = ?"
                " ORDER BY version DESC LIMIT 1",
                (reconstruction_id,),
            ).fetchone()
        return self.conn.execute(
            "SELECT * FROM report_version WHERE reconstruction_id = ? AND version = ?",
            (reconstruction_id, version),
        ).fetchone()


class Store(_Phase1Mixin):
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
        tables = [
            "source_fetch",
            "station",
            "audio_block",
            "utterance",
            "callsign_mention",
            "atc_event",
            "reconstruction",
            "report_version",
            "job",
            "audit_log",
        ]
        return {
            t: int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"])
            for t in tables
        }
