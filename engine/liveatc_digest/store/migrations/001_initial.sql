-- Core provenance, audio, and audit tables. Later phases add tracks, NTSB, reports, chat.

CREATE TABLE IF NOT EXISTS source_fetch (
    id          INTEGER PRIMARY KEY,
    provider    TEXT NOT NULL,
    url         TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    bytes       INTEGER NOT NULL,
    content_type TEXT,
    fetched_at  TEXT NOT NULL,            -- ISO-8601 UTC
    licence     TEXT,
    UNIQUE (provider, url, sha256)
);
CREATE INDEX IF NOT EXISTS ix_source_fetch_url ON source_fetch (url);

CREATE TABLE IF NOT EXISTS station (
    id          INTEGER PRIMARY KEY,
    icao        TEXT NOT NULL,
    identifier  TEXT NOT NULL,            -- LiveATC mount, e.g. kbed_twr
    title       TEXT NOT NULL,
    role        TEXT NOT NULL,            -- clearance|ground|tower|approach|departure|center|ctaf|unknown
    up          INTEGER NOT NULL DEFAULT 1,
    frequencies_json TEXT NOT NULL,
    archive_id  TEXT,                     -- e.g. KBED-Twr (resolved lazily)
    seen_at     TEXT NOT NULL,
    UNIQUE (identifier)
);

CREATE TABLE IF NOT EXISTS audio_block (
    id          INTEGER PRIMARY KEY,
    station_id  INTEGER NOT NULL REFERENCES station(id),
    icao        TEXT NOT NULL,
    start_utc   TEXT NOT NULL,
    end_utc     TEXT NOT NULL,
    source_fetch_id INTEGER REFERENCES source_fetch(id),
    status      TEXT NOT NULL,            -- fetched|missing|expired|future
    UNIQUE (station_id, start_utc)
);

CREATE TABLE IF NOT EXISTS utterance (
    id          INTEGER PRIMARY KEY,
    audio_block_id INTEGER NOT NULL REFERENCES audio_block(id),
    start_utc   TEXT NOT NULL,
    end_utc     TEXT NOT NULL,
    text        TEXT NOT NULL,
    normalized_text TEXT,
    asr_provider TEXT NOT NULL,
    asr_model   TEXT NOT NULL,
    confidence  REAL,
    phase       TEXT,                     -- ground|terminal|enroute
    feed_role   TEXT
);
CREATE INDEX IF NOT EXISTS ix_utterance_block ON utterance (audio_block_id, start_utc);

CREATE VIRTUAL TABLE IF NOT EXISTS utterance_fts USING fts5(
    text, content='utterance', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS utterance_ai AFTER INSERT ON utterance BEGIN
    INSERT INTO utterance_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS utterance_ad AFTER DELETE ON utterance BEGIN
    INSERT INTO utterance_fts(utterance_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS utterance_au AFTER UPDATE ON utterance BEGIN
    INSERT INTO utterance_fts(utterance_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO utterance_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS job (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    params_json TEXT NOT NULL,
    status      TEXT NOT NULL,            -- pending|running|done|failed
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    utc         TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    details_json TEXT
);
