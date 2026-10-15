-- Phase 1: extraction results and versioned reports.

CREATE TABLE IF NOT EXISTS callsign_mention (
    id          INTEGER PRIMARY KEY,
    utterance_id INTEGER NOT NULL REFERENCES utterance(id) ON DELETE CASCADE,
    raw         TEXT NOT NULL,
    callsign    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    span_start  INTEGER NOT NULL,
    span_end    INTEGER NOT NULL,
    confidence  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_callsign_utt ON callsign_mention (utterance_id);
CREATE INDEX IF NOT EXISTS ix_callsign_cs ON callsign_mention (callsign);

CREATE TABLE IF NOT EXISTS atc_event (
    id          INTEGER PRIMARY KEY,
    utterance_id INTEGER NOT NULL REFERENCES utterance(id) ON DELETE CASCADE,
    type        TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    text        TEXT NOT NULL,
    span_start  INTEGER NOT NULL,
    span_end    INTEGER NOT NULL,
    confidence  REAL NOT NULL,
    source      TEXT NOT NULL DEFAULT 'rules'   -- rules | llm
);
CREATE INDEX IF NOT EXISTS ix_event_utt ON atc_event (utterance_id);

CREATE TABLE IF NOT EXISTS reconstruction (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,                  -- digest | reconstruct | case
    icao        TEXT,
    start_utc   TEXT,
    end_utc     TEXT,
    params_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_version (
    id          INTEGER PRIMARY KEY,
    reconstruction_id INTEGER NOT NULL REFERENCES reconstruction(id),
    version     INTEGER NOT NULL,
    body_md     TEXT NOT NULL,
    evidence_map_json TEXT NOT NULL,
    llm_provider TEXT,
    llm_model   TEXT,
    prompt_hash TEXT,
    inputs_hash TEXT NOT NULL,
    validator_log_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (reconstruction_id, version)
);
