# Roadmap (draft)

Phases are ordered so that each one ships something usable from the CLI, and so the
safety safeguards exist before any accident-related output is produced.

## Phase 0 — Foundation
- Restructure into `engine/` package with `pyproject.toml`, console script `latc`.
- Fold `liveatc_downloader` into the package; add caching, retries, UTC block planning.
- Config file + OS keychain for API keys. SQLite store with migrations. Audit log.
- Test fixtures: a 2-minute public-domain ATC clip, one readsb trace file, a CIFP excerpt.

**Done when:** `latc stations KBED` and `latc fetch kbed_twr --from … --to …` work with cache hits.

## Phase 1 — Digest MVP
- Segmentation with absolute UTC timestamps; `TranscriptionProvider` (local faster-whisper,
  OpenAI) with per-utterance confidence.
- Rule-based callsign/clearance extraction; LLM structured extraction with span check.
- Phase classification (ground / terminal / enroute) from feed role + content.
- Digest generator with citations; grounding validator (citation check + speculation lint).
- Markdown and JSON output; reports saved as versions.

**Done when:** `latc digest KBED --from … --to …` yields a cited digest and a stored report.

## Phase 2 — ADS-B and map replay
- `TrackProvider`: adsb.lol history (free), ADS-B Exchange sample, live REST; Parquet cache.
- Registration ↔ hex resolution via FAA registry download.
- Transcript ↔ track joining with confidence and reason.
- `latc reconstruct` exports a self-contained HTML replay (MapLibre, timeline, transcript).

**Done when:** a one-hour window at a towered airport replays with aircraft + transmissions.

## Phase 3 — Charts and IFR overlays
- CIFP parser → GeoJSON procedures; NASR airports/runways/navaids.
- FAA VFR GeoTIFF tiling to MBTiles; d-TPP plate georef warping (fallback: side-by-side).
- Procedure overlay auto-selected from approach/departure clearances in the transcript.

**Done when:** an aircraft cleared for an approach shows its track over that procedure.

## Phase 4 — NTSB case toolkit
- CAROL client + monthly database fallback; docket document fetch and transcript detection.
- Case builder pipeline; fixed report template; quote check for findings/probable cause.
- Data-gap reporting. Watch mode: daily poll of NTSB prelims / FAA ASIAS, auto-archive
  LiveATC blocks and ADS-B for new events.

**Done when:** `latc case build --ntsb <id>` produces a validated, versioned case study.

## Phase 5 — Desktop app
- Tauri 2 shell, Python sidecar (FastAPI), React + MapLibre + deck.gl.
- Screens: Digest, Reconstruct, Case Studies, Settings. Timeline scrubber syncing audio,
  transcript, and map.

**Done when:** every Phase 1–4 CLI flow is reachable from the app without a terminal.

## Phase 6 — Educational chat
- Corpus ingest: eCFR Title 14 (date-versioned), ADs by make/model (DRS), FAA handbooks
  and ACs. Chunking with section ids; local embeddings.
- Citation-only answering with refusal when retrieval is empty; regulatory grounding check.
- Chat panel in the app; `latc chat` in the CLI.

**Done when:** every chat answer carries verifiable citations and the validator passes.

## Phase 7 — Audit and export
- Report diffing between versions; export bundles (Markdown + JSON evidence map + figures).
- Optional Postgres backend; workspace import/export.

## Cross-cutting
- ASR quality benchmark on a small labelled set of US ATC audio to pick default checkpoints.
- Licence/attribution file generated per export (ODbL, LiveATC terms, public-domain notes).
