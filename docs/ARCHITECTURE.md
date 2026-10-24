# LiveATC Digest — System Design (Workshop Draft v0.1)

Status: **draft for discussion**. Decisions taken so far are logged in §12; remaining open
questions are in §11.

This document describes the target architecture for turning the current `ingest.py`
prototype into three layered products that share one engine:

1. **Digest** — download LiveATC recordings for an airfield and time window, transcribe them,
   and produce a time-stamped, summarized reconstruction of ground / terminal / enroute activity.
2. **Reconstruct** — fuse the transcript with ADS-B tracks and chart overlays (VFR raster,
   IFR procedures from FAA data) to replay the activity on a map and timeline.
3. **Case Study** — drive the same engine from NTSB accident records to build a factual,
   non-speculative spatiotemporal reconstruction and report, with an educational chat
   grounded only in cited source material (14 CFR, ADs, handbooks, ACs).

All three are available from a **CLI** (power users) and a **minimal desktop app** (everyone
else). Minimum functionality uses only public data; paid data products plug in via API keys.

---

## 1. Guiding principles

| Principle | What it means in practice |
|---|---|
| **Safety first, never speculate** | The system reports what the data shows. Causes appear only when the NTSB has published them, quoted verbatim with a citation. No "the pilot should have…" anywhere in generated output. Enforced by prompt rules **and** a post-generation validator (see §7.4). |
| **Every claim is traceable** | Every sentence in a digest, reconstruction, or report carries a provenance reference: utterance id, ADS-B point range, NTSB field, or regulatory section. Unsupported sentences are rejected before the user sees them. |
| **Public data by default** | LiveATC, adsb.lol history, FAA charts/CIFP/d-TPP, NTSB CAROL/dockets, eCFR, FAA handbooks are all free. Paid providers (ADS-B Exchange, FlightAware, commercial chart APIs) are optional adapters behind the same interfaces. |
| **One engine, two front ends** | The CLI and the desktop app call the same Python engine. The GUI never has logic the CLI lacks. |
| **Honest about uncertainty** | ATC ASR word error rates are high (roughly 30% on American ATC audio for the best open fine-tunes). Confidence scores are stored per utterance and surfaced in the UI. |
| **Auditable** | Reconstructions and case studies are versioned in a local database with input hashes, model versions, and prompts, so a result can be re-derived or challenged later. |

---

## 2. System overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Front ends                                                              │
│   ┌────────────────────┐      ┌────────────────────────────────────────┐ │
│   │ CLI  (`latc`)      │      │ Desktop app (Tauri 2 + React/MapLibre) │ │
│   └─────────┬──────────┘      └──────────────────┬─────────────────────┘ │
│             │ Python API                         │ local HTTP/WS (sidecar)│
├─────────────┴────────────────────────────────────┴───────────────────────┤
│  Engine  (Python package `liveatc_digest`)                               │
│                                                                          │
│   jobs/          pipelines: digest · reconstruct · case                  │
│   sources/       liveatc · adsb (adsblol, adsbx, opensky, aeroapi…)      │
│                  ntsb (carol, avdata, docket) · faa (cifp, dtpp, vfr)    │
│                  regs (ecfr, drs, handbooks)                             │
│   audio/         fetch · segment · normalize · transcribe (providers)    │
│   nlp/           callsign & clearance extraction · phase classification  │
│   fusion/        transcript ↔ track joining · event timeline             │
│   report/        digest & case-study generation · grounding validator    │
│   chat/          RAG over regulatory corpus · citation-only answering    │
│   store/         SQLite (+FTS5) · raw-data cache · audit log             │
│   geo/           CIFP → procedure geometry · chart tiling · georef       │
└──────────────────────────────────────────────────────────────────────────┘
```

The engine is a normal importable Python package. The desktop app bundles it with
PyInstaller and launches it as a Tauri *sidecar* exposing a local FastAPI server
(REST for commands, WebSocket for job progress and streaming transcription). The CLI
imports the package directly.

---

## 3. Data sources

### 3.1 Free / public (minimum functionality)

| Need | Source | Access | Notes & constraints |
|---|---|---|---|
| ATC audio | LiveATC.net archives | `https://archive.liveatc.net/{icao}/{feed}-{Mon-DD-YYYY}-{HHMM}Z.mp3`, 30-minute blocks, scraped station list (existing `liveatc_downloader`) | **Archives are retained ~30 days.** This is the single biggest constraint on the NTSB toolkit (see §7.2). Personal/non-commercial use per LiveATC terms; we never redistribute audio. |
| Historical ADS-B | adsb.lol `globe_history_YYYY` GitHub releases | One gzipped tarball per day; readsb `traces/` JSON per ICAO hex | ODbL licence. Covers 2023 → 2025 as archived repos; confirm a 2026 repo exists. Multi-GB per day, so we fetch per-day and extract only the hexes we need. |
| Historical ADS-B (sample) | ADS-B Exchange sample data | `samples.adsbexchange.com/traces/yyyy/mm/dd/xx/` | Only the 1st of each month is free. Same readsb trace format. |
| Live ADS-B | adsb.lol / airplanes.live / adsb.fi REST | `/v2/icao/{hex}`, `/v2/point/{lat}/{lon}/{nm}` | Used for the "watch" mode that captures tracks in real time. |
| IFR procedure geometry | FAA CIFP (ARINC 424) | 28-day cycle zip | Raw ARINC 424 records; we parse SIDs, STARs, approaches, waypoints, airways into GeoJSON. No data dictionary supplied; use the public ARINC 424 field layout. |
| IFR plates | FAA d-TPP | 28-day cycle; XML metafile + PDF per procedure | PDFs embed georeferencing (FAA format, not widely supported). Plan A: parse the embedded georef and warp the raster. Plan B: render CIFP vectors and show the plate PDF beside the map. |
| VFR charts | FAA VFR raster charts (sectional, TAC) | GeoTIFF zips, 56-day cycle | Public domain. Tiled locally with GDAL into XYZ/MBTiles. |
| Airports, runways, navaids, airspace | FAA NASR (28-day) and FAA ADDS ArcGIS open data | CSV/Shapefile/GeoJSON | Runway geometry is essential for ground-phase reconstruction. |
| Accident records | NTSB CAROL | `data.ntsb.gov/carol-main-public/api/Query/Main` (undocumented JSON query API used by community clients) and the official **monthly aviation database** (`avall` MDB download) | Treat CAROL's JSON API as "works today, undocumented". The monthly database is the official bulk source and the fallback. Fields we need: NTSB number, event date/time (local + UTC), location, registration, aircraft make/model, nearest airport, report status (prelim/factual/final), probable cause, findings. |
| Accident dockets | NTSB Docket Management System | `data.ntsb.gov/Docket/...` document lists and PDFs | **Dockets frequently contain the official ATC transcript and sometimes the audio.** When present these are preferred over our own LiveATC transcription. |
| Recent accidents (early warning) | FAA ASIAS preliminary accident/incident reports; NTSB preliminary reports | daily | Drives the "watch" mode that captures LiveATC audio before it expires. |
| Regulations | eCFR API (Title 14) | `api/versioner/v1/full/{date}/title-14.xml`, structure endpoint | Versioned by date, so the chat cites the regulation **as it stood on the event date**. No key required. |
| Airworthiness Directives | FAA Dynamic Regulatory System | web only; no documented API | Fetched per make/model via the DRS search export; cached locally with the download date. |
| Handbooks & ACs | FAA (PHAK, AFH, IFH, IPH, AIM, ACs) | PDFs, public domain | Chunked and indexed for the educational chat. |

### 3.2 Paid / key-gated (optional adapters)

| Need | Provider | Key |
|---|---|---|
| Historical ADS-B, any date | ADS-B Exchange (commercial), FlightAware AeroAPI, Flightradar24 | API key or dataset agreement |
| Research ADS-B | OpenSky Network Trino | account (non-commercial research only) |
| Cloud transcription | OpenAI (`whisper-1`, `gpt-4o-transcribe`), Deepgram, AssemblyAI | API key |
| LLM for summaries / chat | OpenAI, Anthropic, local via Ollama | API key or none |
| Commercial charts / procedures | Any provider with a tile or plate API (treated as a raster/vector tile adapter) | API key |

Keys are stored in the OS keychain (`keyring`), never in the database or project files.

### 3.3 Licensing and terms to respect

- LiveATC: personal use; no redistribution of audio. We cache locally and export only derived text.
- adsb.lol history: ODbL. **Retrieved ADS-B point data is never exported** from the tool
  (no CSV/GeoJSON/KML of positions, and the per-case database is not a sharing format).
  Exports contain only rendered map figures and derived event text, with attribution.
- ADS-B Exchange: no bulk resale/redistribution (same no-export rule applies).
- OpenSky: non-commercial research only; must not be the default adapter.
- NTSB, FAA, eCFR: US government works, public domain.

---

## 4. Engine modules

### 4.1 `sources/liveatc`
- `list_stations(icao) -> [Station]` (existing scraper, hardened with caching and retry).
  LiveATC's HTML pages are behind bot protection that can answer with a human-verification
  page. The engine detects this (`ChallengeError`) and **does not attempt to bypass it**; it
  tells the person to register the feed manually (`latc station add`), import a page they
  opened in their own browser (`--from-html`), or pass `--archive-id`. Recordings are
  downloaded directly from `archive.liveatc.net`, which needs none of those pages.
- `plan_blocks(station, start_utc, end_utc) -> [Block]`: expands a time range into 30-minute
  archive blocks aligned to :00/:30 Z.
- `fetch_block(block) -> Path`: content-addressed cache under `~/.liveatc-digest/cache/audio/`.
- Station-to-role mapping (Clearance / Ground / Tower / Approach / Departure / Center / CTAF)
  inferred from feed titles and frequencies, used for phase classification later.

### 4.2 `audio`
- Segmentation on silence (existing `audio_utils`), with **absolute UTC timestamps** =
  block start + offset. Each segment becomes an `Utterance` candidate.
- Normalization and optional noise reduction before ASR.
- `TranscriptionProvider` interface:
  - `local-whisper`: `faster-whisper` (CTranslate2). Default checkpoint selectable; recommend
    an ATC fine-tune (e.g. `jlvdoorn/whisper-large-v3-atco2-asr` converted to CT2) with a
    domain prompt listing airport callsigns, runways and local fixes to bias decoding.
  - `openai`: existing cloud path.
  - Others added behind the same interface.
- Output per utterance: text, start/end UTC, feed, ASR confidence, provider + model id.
- "Translate" means **transcription to English** plus **phraseology normalization**
  (e.g. "cleared ILS two-seven approach" → structured clearance). Translation into other
  languages is out of scope.

### 4.3 `nlp`
- Callsign extraction: airline telephony → ICAO designator (from the FAA/ICAO telephony
  list), spoken N-numbers from phonetics ("November one two three alpha bravo" → N123AB),
  military/GA shorthand.
- Clearance and instruction extraction into typed events: taxi, line-up-and-wait, takeoff
  clearance, landing clearance, altitude/heading/speed assignments, approach clearances,
  frequency changes, go-around, traffic advisories, emergencies (MAYDAY/PAN-PAN, squawk 7700).
- Two tiers: fast rule/regex tier, then an LLM structured-extraction tier that must return
  spans into the utterance text (so every extracted field points back to words that were
  actually transcribed). Fields without a span are discarded.
- Phase classification per utterance: ground / terminal / enroute, from feed role +
  content.

### 4.4 `sources/adsb` and `fusion`
- `TrackProvider` interface: `traces_for(hex | registration | callsign, start, end)` and
  `traces_near(lat, lon, radius_nm, start, end)`.
- Normalized track schema: timestamp, lat, lon, baro alt, geo alt, GS, track, vertical rate,
  squawk, callsign, source, receiver quality flags. Stored in Parquet (cache) and summarized
  in SQLite.
- Joining transcript to tracks: callsign/registration match first; otherwise time-and-
  geometry heuristics (an aircraft cleared to land runway 27 at 14:03Z that is on final for
  27 at 14:03Z). Every join carries a confidence and a reason.
- Event timeline = extracted ATC events + derived track events (takeoff roll, airborne,
  level-off, approach intercept, touchdown, taxi clear) merged by time.

### 4.5 `geo`
- CIFP (ARINC 424) parser → procedure legs → GeoJSON (with leg types resolved: TF, CF, DF,
  RF arcs, holds). Airport, runway, and navaid layers from NASR.
- Chart tiling: FAA VFR GeoTIFF → MBTiles; d-TPP plate georef → warped PNG overlay with
  opacity control, registered over the VFR/ground base map.
- Base map: OpenFreeMap/OpenStreetMap vector tiles (no key) with a plain style.

### 4.6 `report`
- **Digest**: a structured summary by phase (ground / terminal / enroute) and by aircraft,
  with a chronological highlights list. Each bullet cites utterance ids; the renderer turns
  them into clickable timestamps that seek the audio.
- **Case study report**: fixed template (§7.3). Generated text is passed through the
  grounding validator (§7.4) before storage.

### 4.7 `chat`
- Retrieval-augmented chat scoped to a saved reconstruction or case study.
- Corpus: eCFR Title 14 (as of event date), ADs for the aircraft make/model, FAA handbooks
  and ACs, the NTSB report text itself. Chunked with section identifiers retained.
- Answering rule: the model may only state regulatory or guidance content that appears in
  retrieved chunks, and must cite them (`14 CFR §91.175(c)`, `AD 2020-xx-xx`, `PHAK ch. 10
  p. 10-4`). If nothing relevant is retrieved it says so instead of answering from memory.
  Responses about the accident itself are limited to what the report and reconstruction
  contain.

### 4.8 `store`
- **One SQLite file per reconstructed case study** (`<ntsb-number>.sqlite`), containing the
  case record, utterances, events, joins, report versions, chat history, and audit log.
  FTS5 for transcript and report search. Digests and ad-hoc reconstructions live in a
  workspace database (`workspace.sqlite`) until promoted to a case.
- Raw ADS-B points are **not** stored in the case database; they stay in the local Parquet
  cache and the case database references them by hash. This keeps the case file free of
  licensed point data so it can be archived or audited without redistributing ADS-B.
- Raw downloads in a content-addressed cache keyed by SHA-256; the database stores hashes,
  URLs, fetch time, and provider, so every derived artifact can be traced to bytes.

---

## 5. Pipelines

### 5.1 Digest (Phase 1 MVP)
```
latc digest KBED --from 2024-07-28T00:00Z --to 2024-07-28T01:00Z --feeds twr,gnd \
     --asr local-whisper --llm openai:gpt-4o-mini
```
1. Resolve feeds for the airport; plan 30-minute blocks; fetch with cache.
2. Segment, transcribe, timestamp; persist utterances.
3. Extract callsigns/events; classify phases.
4. Generate digest with citations; validate grounding; persist as a `Report` version.
5. Emit Markdown/JSON; in the app, render transcript + audio scrubber.

### 5.2 Reconstruct (Phase 2–3)
Digest + ADS-B tracks for the same window and area → fused timeline → map replay with
transcript bubbles anchored to aircraft positions; IFR procedure overlays for aircraft
whose clearances name a procedure.

### 5.3 Case study (Phase 4)
```
latc case build --ntsb ERA24LA123       # or --registration N123AB --date 2024-07-28
```
1. Fetch the NTSB record (CAROL → fallback monthly DB); determine event UTC window,
   location, registration, aircraft type, nearest airports, report status.
2. Fetch docket documents and select the audio/transcript source in this fixed priority:
   1. **NTSB audio** from the docket (transcribed by our ASR, timestamps aligned to the
      NTSB transcript when one exists).
   2. **NTSB transcript** from the docket (text only; the report notes that audio itself is
      not available).
   3. **LiveATC audio captured by watch mode** (§5.4) while the archive was still live.
   4. None of the above: the report states **"Audio not available for the accident in
      question"** and the reconstruction proceeds on ADS-B and NTSB narrative alone.
3. Resolve ADS-B hex from registration (FAA registry) and fetch traces for the window.
4. If the event is still inside the LiveATC retention window and no NTSB audio exists,
   fetch LiveATC blocks for airports along the track now and tag them to the case.
5. Build reconstruction (§5.2) and the report (§7.3); validate; save with full provenance.

### 5.4 Watch mode (fallback for the 30-day gap)
A scheduled job polls NTSB preliminary reports and FAA ASIAS daily, and for each new event
immediately archives the relevant LiveATC blocks and live ADS-B history into the local
cache, tagged by event, so a case study can be built months later when the final report
arrives. Watch mode is a **fallback**: NTSB docket audio always takes priority when it
exists, and when neither exists the case reports that audio is not available.

---

## 6. Front ends

### 6.1 CLI (`latc`)
Subcommands: `stations`, `fetch`, `transcribe`, `digest`, `adsb`, `charts`, `reconstruct`,
`ntsb search|show|docket`, `case build|list|show|export`, `watch`, `chat`, `db`.
All commands support `--json` output and read config from `~/.liveatc-digest/config.toml`.
Long jobs are resumable (job state is in the database).

### 6.2 Desktop app
- Tauri 2 shell (small binary, native webview) + React + MapLibre GL + deck.gl for tracks.
- Three primary screens: **Digest**, **Reconstruct**, **Case Studies**. A single settings
  pane for API keys and model choices.
- Core interaction: a timeline scrubber synchronizes audio playback, transcript highlight,
  and aircraft positions on the map. Clicking a sentence in a summary seeks to its evidence.
- Educational chat is a side panel on any saved reconstruction or case study; every answer
  shows its citations inline with links to the source passage.
- Minimal visual language: one accent color, system fonts, no dashboards-for-the-sake-of-it.

---

## 7. NTSB case reconstruction toolkit — safeguards

### 7.1 Scope
Input is an NTSB case identifier or (registration, date). Output is a reconstruction plus a
report. The toolkit never searches for "similar accidents" or draws comparisons on its own.

### 7.2 Data availability honesty
Each case carries a data-gap list (no LiveATC audio, partial ADS-B coverage, preliminary
report only). Gaps are shown prominently, not buried.

### 7.3 Report template (fixed sections)
1. Case identification (NTSB number, date, location, aircraft, report status).
2. Data sources used, with retrieval timestamps and hashes.
3. Factual timeline (merged ATC + track events, each cited).
4. Spatiotemporal reconstruction (map figures + narrative of positions and transmissions).
5. NTSB findings — verbatim quotes from the NTSB report only.
6. Probable cause — verbatim from the NTSB only; if not yet issued: "The NTSB has not
   issued a probable cause for this event."
7. Data gaps and limitations.
8. Related regulations and guidance (from the chat corpus, citations only; no application
   to the pilot's actions).

### 7.4 Grounding validator
Runs on every generated digest, reconstruction narrative, report, and chat answer:
- **Citation check**: each sentence must reference at least one evidence id; sentences
  without one are removed and logged.
- **Quote check**: text in the findings/probable-cause sections must be a substring of the
  NTSB source text.
- **Speculation lint**: rejects counterfactual or evaluative language ("should have",
  "could have avoided", "failed to", "pilot error", "if the pilot had") unless inside a
  verbatim NTSB quote. The list is configurable and the rejection is logged for audit.
- **Regulatory grounding**: any regulation, AD, or handbook reference in output must match a
  retrieved chunk id; otherwise the sentence is removed.

---

## 8. Data model (SQLite)

```
workspace          id, created_at
source_fetch       id, provider, url, sha256, fetched_at, bytes, licence
audio_block        id, station_id, icao, start_utc, end_utc, source_fetch_id
utterance          id, audio_block_id, start_utc, end_utc, text, normalized_text,
                   asr_provider, asr_model, confidence, phase, feed_role
callsign_mention   id, utterance_id, raw, resolved_callsign, registration, span, confidence
atc_event          id, utterance_id, type, fields_json, span, confidence
track              id, provider, hex, registration, callsign, start_utc, end_utc, parquet_path
track_event        id, track_id, type, utc, lat, lon, alt, fields_json
join               id, utterance_id|atc_event_id, track_id, method, confidence, reason
ntsb_case          id, ntsb_number, event_utc, lat, lon, registration, make, model,
                   report_status, probable_cause_text, findings_json, source_fetch_id
docket_document    id, ntsb_case_id, title, url, kind (transcript|audio|other), source_fetch_id
reconstruction     id, kind (digest|reconstruct|case), params_json, created_at
report_version     id, reconstruction_id, version, body_md, evidence_map_json,
                   llm_provider, llm_model, prompt_hash, inputs_hash, validator_log_json
chat_session       id, reconstruction_id, created_at
chat_message       id, chat_session_id, role, text, citations_json, retrieval_ids_json
reg_chunk          id, corpus, section_id, as_of_date, text, embedding
audit_log          id, utc, actor, action, target, details_json
```

`report_version` rows are append-only; a regenerated report creates a new version.
`track.parquet_path` points at the local cache; case databases carry `track_event` rows and
rendered figures, not raw positions (see §3.3 and §4.8).

---

## 9. Proposed repository layout

```
liveatc-digest/
├── engine/
│   ├── pyproject.toml            # package `liveatc_digest`, console script `latc`
│   ├── liveatc_digest/
│   │   ├── sources/{liveatc,adsb,ntsb,faa,regs}/
│   │   ├── audio/  nlp/  fusion/  geo/  report/  chat/  store/  jobs/
│   │   ├── cli/                  # typer app
│   │   └── server/               # FastAPI sidecar for the desktop app
│   └── tests/                    # fixtures: short audio clips, trace JSON, CIFP excerpt
├── apps/desktop/                 # Tauri 2 + React + MapLibre
├── docs/                         # this document, data-source notes, ADRs
└── scripts/                      # chart tiling, corpus ingest
```

The `liveatc_downloader` submodule (SSH URL, currently not checked out) is folded into
`engine/liveatc_digest/sources/liveatc/` with attribution, since the engine needs to
own caching, retries, and timestamp handling.

---

## 10. Key technology choices (proposed)

| Area | Choice | Why |
|---|---|---|
| Engine language | Python 3.11+ | Existing code; Whisper, pydub, GeoPandas/Shapely, DuckDB/Parquet ecosystem. |
| Local ASR | faster-whisper + ATC fine-tune | 4× faster than openai-whisper on CPU, word timestamps, same checkpoints via CT2 conversion. |
| LLM layer | provider-agnostic (`litellm`-style thin wrapper) | OpenAI is the v0 default; other cloud providers are adapters. A local LLM is **not** required for v0. |
| Track storage | Parquet + DuckDB | adsb.lol days are large; columnar scans by hex/time are cheap. |
| Database | SQLite + FTS5 | Zero-config, single file, portable, auditable. |
| Desktop shell | Tauri 2 | Small binaries, native webview, first-class sidecar support. |
| Map | MapLibre GL + deck.gl | Open source, raster + vector overlays, GPU track rendering. |
| Chart processing | GDAL | GeoTIFF → tiles; PDF georef warping. |
| CLI | Typer + Rich | Fast to build, good help output, `--json` everywhere. |

---

## 11. Open questions (still open)

1. **Transcript-only cases.** When the NTSB docket has a transcript but no audio, the
   report will say "audio not available" and use the transcript as the text source. Confirm
   that a transcript-only case is acceptable for full reconstruction, or whether it should
   be labelled differently (e.g. "reconstruction from NTSB transcript").
2. **Rendered maps as exports.** Under the no-export rule, exports contain map figures that
   depict the track but no point data. Is a rendered image acceptable, or should exports
   omit track imagery entirely?
3. **Digest storage.** Cases get one SQLite file each. Should digests and ad-hoc
   reconstructions share one `workspace.sqlite` (proposed), or also get one file each?
4. **Educational corpus scope.** Proposed start: 14 CFR Parts 1, 61, 91, 97, 121, 135;
   AIM; PHAK; AFH; IFH; IPH; ACs 61-series and 90-series; ADs by make/model. Anything to add
   or drop?
5. **Desktop platforms.** macOS + Windows + Linux? Is code signing / notarization in budget
   for the first GUI release?
6. **Local ASR hardware floor.** Slowest supported machine for local Whisper (CPU-only
   laptop vs. GPU)? This sets the default checkpoint size; cloud ASR remains the fallback.
7. **Watch-mode polling.** Daily polling of NTSB preliminary reports and FAA ASIAS: run as a
   background service installed by the app, or only while the app/CLI is running?

## 12. Decisions log

| # | Decision | Date |
|---|---|---|
| D1 | Audio source priority for cases: NTSB docket audio → NTSB docket transcript → LiveATC audio captured by watch mode → "Audio not available for the accident in question". Watch mode is a fallback only. | 2026-10-15 |
| D2 | CLI first; the desktop app is a thin layer over the same engine. | 2026-10-15 |
| D3 | "Translate" = transcription to English + phraseology normalization. No foreign-language translation. | 2026-10-15 |
| D4 | LLM layer is provider-agnostic; OpenAI is the v0 default; no local LLM requirement for v0. | 2026-10-15 |
| D5 | One SQLite database per reconstructed case study. | 2026-10-15 |
| D6 | Retrieved ADS-B data is never exported, to stay within free-licence terms (ODbL, ADS-B Exchange). Raw points stay in the local cache, outside the case database. | 2026-10-15 |
