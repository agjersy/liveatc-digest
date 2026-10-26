# NTSB Case-Study Tool — Architecture and Product Specification (Workshop Draft v0.1)

Status: **draft for discussion**. Companion to `ARCHITECTURE.md` (§7 sketched this tool;
this document supersedes that sketch). Decisions already taken in the architecture decisions
log (D1–D6) apply here unchanged: NTSB docket audio first, CLI first, provider-agnostic LLM,
one SQLite file per case, no export of ADS-B point data.

---

## 1. What it is

A parallel tool to the LiveATC digest, sharing the same engine, that lets a pilot or
instructor:

1. **Find** an NTSB accident or incident investigation (by NTSB number, registration, date,
   place, aircraft type, or free text) and **retrieve** the official report(s) and docket.
2. **Read** a structured, cited summary of what the NTSB documented, with the probable cause
   quoted verbatim or marked as not yet issued.
3. **See** the flight as a 3D trajectory from ADS-B (and docket radar data where present),
   synchronized with the NTSB timeline and any ATC transcript, over terrain and charts.
4. **Learn** from the official FAA references that relate to the facts of the case: 14 CFR,
   the AIM, Advisory Circulars, Airworthiness Directives for the type, the published IFR
   procedure in use, and FAA handbooks, each retrieved and cited, never paraphrased from memory.
5. **Ask** questions in a chat that answers only from the case materials and the retrieved
   references, and says so when it cannot.

It never speculates about cause, never grades the pilot, and never suggests what anyone
should have done. The educational value comes from putting the NTSB's own findings next to
the FAA's own words.

### 1.1 Who it is for

| Persona | Needs | Typical session |
|---|---|---|
| Student / private pilot | Understand a well-known accident; see the regulation or AIM text that applies to the situation | Open a case, read the summary, scrub the 3D track, ask "what does 91.175 say about the missed approach point?" |
| CFI / ground instructor | Build a lesson around a case; need exact citations and a replay to show in class | Search by type and phase of flight, open two or three cases, export each case bundle (summary + figures + citation list) |
| Flight-school safety officer / FAASTeam rep | Review recent local accidents; check which ADs and ACs apply to the fleet | Watch list by region, open preliminary reports as they publish, revisit when the final report lands |
| Researcher / journalist | Trace every claim to a source | Evidence map, provenance hashes, docket documents, raw report text |

### 1.2 Non-goals

- Determining or suggesting cause, contributing factors, or "lessons" not stated by the NTSB.
- Comparing accidents or computing accident statistics (that is a different product).
- Legal interpretation of regulations. The tool quotes the regulation and the FAA's own
  guidance about it; it does not say whether a regulation was violated.
- Real-time flight tracking.
- Reproducing or redistributing ADS-B point data or copyrighted chart products.

---

## 2. User journeys

**J1 — Open a known case.** Enter `ERA24LA123` (or a registration and date). The tool fetches
the case record, downloads the report PDFs and docket index, resolves the aircraft's ICAO hex,
pulls ADS-B traces for the event window, builds the case file, and opens the case screen with
summary, timeline, 3D map, references, and chat.

**J2 — Browse recent accidents.** Filter by date range, state, aircraft category, injury
severity, report status. Results come from a locally indexed copy of the NTSB monthly database
so browsing is instant and offline; opening a case fetches the live record.

**J3 — Study the approach.** The report says the aircraft was on the RNAV (GPS) RWY 29
approach. The tool overlays the CIFP geometry for that procedure in 3D under the track, shows
the plate from d-TPP for the cycle in effect on the event date, and lists AIM 5-4-5 and
14 CFR 91.175 in the references pane with the retrieved text.

**J4 — Check the fleet.** Open a case for a Cessna 172S. The references pane lists the ADs
applicable to the airframe and engine model as recorded by the NTSB, with issue dates, linked
to the FAA source. The chat can quote an AD; it will not say whether compliance was a factor
unless the NTSB report says so.

**J5 — Follow a case over time.** A preliminary report is out; the final is a year away. The
case file records the preliminary, and a watch marks it to re-fetch when the status changes.
Each refetch creates a new report version; the diff shows what the NTSB added.

**J6 — Export for class.** Produce a bundle: summary (Markdown/PDF), map figures (PNG), the
citation list with source URLs and retrieval dates, and the evidence map. No ADS-B points.

---

## 3. Product principles specific to this tool

1. **The NTSB speaks for cause; the FAA speaks for rules.** The tool arranges and cites; it
   does not interpret. Every generated sentence cites a report paragraph, a docket document,
   a track-derived event, or a retrieved reference chunk.
2. **Facts are typed by source.** The UI visually distinguishes: NTSB statements, docket
   documents, ADS-B-derived observations, FAA reference text, and tool-derived computations
   (e.g., altitude above terrain). A reader always knows who is talking.
3. **"Related reading" is not "what went wrong".** FAA references are attached to the facts
   the NTSB recorded (phase of flight, procedure flown, weather, aircraft type, findings
   categories) through a curated, inspectable mapping, and are labelled as related reading.
4. **Report status is always visible.** Preliminary, factual, and final reports are labelled;
   the probable-cause section reads "not yet issued" until the NTSB issues it.
5. **Gaps are stated.** No ADS-B, no docket, no ATC transcript, no plate for that cycle: each
   gap appears in the case header, not in a footnote.
6. **Public data suffices.** Everything in the minimum product comes from NTSB, FAA, eCFR,
   OpenStreetMap, open terrain tiles, and adsb.lol. Paid ADS-B and chart products are
   optional adapters.

---

## 4. Feature specification

### 4.1 Search and retrieval

- **Sources** (in order of preference; see §9 for verification status):
  1. NTSB Aviation Case API (`api.ntsb.gov`, listed on data.gov with a data dictionary).
  2. CAROL query/export endpoints used by the CAROL web app (undocumented; community
     clients exist).
  3. NTSB monthly aviation database download (official bulk; MDB → imported into a local
     SQLite index for offline browse).
- **Query fields:** NTSB number; registration (N-number); event date or range; city/state/
  country; aircraft make/model/category; injury severity; report status; phase of flight;
  free text over narratives (local FTS over the imported bulk database).
- **Result row:** NTSB number, date (local + UTC), location, make/model, registration, injury
  severity, report status, probable-cause present (yes/no).
- **Report retrieval:** preliminary / factual / final report PDFs and the CAROL "report" text;
  docket table of contents (HTML) → document list (title, type, URL, size). Downloads are
  content-addressed and recorded with URL, hash, retrieval time.
- **Text extraction:** PDF → text with paragraph ids (`p1…pN`) so summaries can cite
  paragraphs. Docket ATC transcript PDFs → utterances with times (reusing the Phase 1
  utterance model with `asr_provider = "ntsb-transcript"`). Docket radar CSVs → tracks with
  `provider = "ntsb-docket"`.

### 4.2 Case record

A case is created from one NTSB number and holds:

| Field group | Content |
|---|---|
| Identity | NTSB number, mode, event type (accident/incident), report status, dates of each report version fetched |
| Event | date/time (local, UTC, timezone source), location, lat/lon (NTSB-provided or geocoded from the stated location, flagged as such), nearest airport, weather conditions as stated, phase of flight, flight rules, operation type (Part 91/135/121…) |
| Aircraft | registration, make, model, series, engine make/model, year, airworthiness category, ICAO hex (from the FAA registry, with the registry file date) |
| People | crew count, passenger count, injuries by severity (as stated) — never names beyond what the report itself prints |
| Narrative | report text by paragraph id; findings list (coded categories as published); probable cause verbatim |
| Docket | document index; extracted transcripts/radar where present |
| Tracks | ADS-B / radar traces referenced by hash (points stay in the local cache) |
| Derived | event timeline (merged), kinematic events (takeoff, level-off, descent rate changes, last point), altitude above terrain |
| Outputs | summary versions, figures, chat sessions, export manifests, audit log |

### 4.3 LLM summarization

- Input: report paragraphs (with ids), findings, probable cause, docket index, derived
  timeline. Output: a fixed template (below). Each sentence must cite `[n:<ntsb>:pNN]`,
  `[d:<doc-id>]`, `[t:<event-id>]`, or `[r:<chunk-id>]`; the Phase 1 validator is extended
  with these citation kinds and with accident-specific speculation patterns (§8).
- Template:
  1. Case at a glance (one paragraph, cited)
  2. Sequence of events as documented (chronological bullets, cited)
  3. Aircraft, crew, and operation as stated
  4. Weather and environment as stated
  5. NTSB findings — verbatim list
  6. Probable cause — verbatim, or "The NTSB has not issued a probable cause for this event (report status: preliminary)."
  7. Data available for this case and gaps
  8. Related official references (from §4.4, with one-line retrieved excerpts)
- Quote check: sections 5–6 must be substrings of the report text. Any mismatch blocks saving.
- Summaries are stored as versions with model id, prompt hash, input hash, validator log.

### 4.4 FAA reference retrieval

**Corpus (public domain, downloaded and indexed locally):**

| Reference | Source | Versioning | Notes |
|---|---|---|---|
| 14 CFR (Parts 1, 23, 25, 39, 43, 61, 67, 91, 97, 119, 121, 135, 141) | eCFR API (`/api/versioner/v1/full/{date}/title-14.xml?part=…`) | **As of the event date** (eCFR is point-in-time) | Section-level chunks with full citation (`14 CFR § 91.175(c)(3)`) |
| Aeronautical Information Manual | FAA AIM HTML/PDF | Edition in effect on the event date where the FAA archive allows; otherwise current, labelled | Paragraph-level chunks (`AIM 5-4-5 b.`) |
| Advisory Circulars | FAA AC library / DRS | Current revision, with the revision letter and date shown | Section-level chunks |
| Airworthiness Directives | FAA DRS, filtered by make/model/engine from the case | AD number, effective date; flag ADs issued **after** the event date | Full AD text |
| Terminal procedures (IFR) | FAA d-TPP (plates) and CIFP (geometry) | The 28-day cycle in effect on the event date (FAA keeps recent cycles; older cycles may be unavailable → gap) | Plate PDF for display; CIFP legs for the 3D overlay |
| Handbooks (PHAK, AFH, IFH, IPH, Weather Handbook, Risk Management Handbook) | FAA PDFs | Current edition, labelled | Section-level chunks |
| Aircraft type data | FAA Type Certificate Data Sheets (DRS) | Current revision | For the airframe/engine named by the NTSB |

**Relevance mapping (the "educational lever"):** references are attached to the case through
three inspectable channels, and each attached reference records which channel produced it:

1. **Structured facts → curated map.** A versioned YAML table maps NTSB-coded facts to
   references. Examples: phase of flight "Approach-IFR final approach" → 14 CFR 91.175,
   AIM 5-4-5 through 5-4-21, IPH ch. 4; weather "IMC" + flight rules "VFR" → 14 CFR 91.155,
   91.157, AIM 7-1, AC 00-6B, PHAK ch. 12; NTSB finding category "Aircraft – Fuel – Fluid
   level" → 14 CFR 91.151, 91.167, PHAK ch. 7; aircraft make/model → ADs and TCDS. The map
   is a project file anyone can read and challenge; it never asserts cause.
2. **Explicit mentions → direct retrieval.** When the NTSB report names a regulation, AD,
   AC, or procedure, the tool retrieves exactly that item.
3. **Semantic retrieval over the corpus** using the report's factual sections as the query,
   restricted to the parts of the corpus in channel 1's scope, and ranked below channels
   1–2. Each hit shows the retrieved passage so the reader can judge relevance.

Every reference is displayed with: citation, source URL, version/effective date, retrieval
date, the channel that attached it, and the retrieved excerpt. A reference is never
summarized without its excerpt being available one click away.

### 4.5 Educational chat

- Scope: one case at a time. Context = the case record, report text, attached references,
  and on-demand retrieval over the local corpus.
- Answering rules: regulatory or guidance content only from retrieved chunks, each cited;
  case facts only from the report/docket/tracks, each cited; if retrieval returns nothing
  relevant, the answer says so and offers the closest corpus section titles.
- Refusals are specific and courteous: "The NTSB report does not state why the pilot
  descended early, and this tool does not speculate about pilot decisions. What the report
  does say is… [n:…:p14]."
- Suggested prompts shown in the UI are factual and reference-oriented ("Show the missed
  approach instructions for this procedure", "What does the AIM say about…", "Which ADs
  applied to this engine model at the time?").
- Every answer passes the validator before display; removed sentences are logged, and the UI
  shows a small "n sentences withheld" indicator that expands to the reasons.

### 4.6 ADS-B retrieval and the 3D trajectory map

**Data acquisition**

- Resolve registration → ICAO hex using the FAA releasable aircraft registry (MASTER file,
  Mode S code), with the registry snapshot date recorded. Fall back to hex seen in the docket
  or in ADS-B data by registration/callsign match.
- Providers behind `TrackProvider`: adsb.lol daily history (free, ODbL, confirmed active for
  2026; one tar per day, extract only `traces/<hex[-2:]>/trace_full_<hex>.json`), ADS-B
  Exchange historical (key), FlightAware AeroAPI (key), OpenSky Trino (research account),
  docket radar CSV (free). Live providers are used only by the watch feature.
- Window: event date ±1 day in UTC to catch the full flight across midnight; clipped to the
  flight containing the event time.
- Normalized points: `t, lat, lon, alt_baro_ft, alt_geom_ft, gs_kt, track_deg, vr_fpm,
  squawk, callsign, source, quality flags (MLAT/ADS-B, position age)`. Stored as Parquet in
  the cache; the case database stores hash references and derived events only (D6).
- Coverage reality: low-altitude segments near the accident site are often missing; piston GA
  aircraft without ADS-B Out produce nothing; docket radar may be the only track. The map
  shows gaps as dashed segments and the header states coverage (first/last point, % of flight
  with positions).

**Derived observations** (each labelled "derived" with the method):

- Altitude above terrain from alt_geom (or alt_baro corrected with the stated altimeter
  setting when geometric altitude is absent) minus terrain elevation sampled from the DEM.
- Kinematic events: takeoff roll start, liftoff, top of climb, level segments, descent-rate
  changes beyond a threshold, turns beyond a threshold, last received position.
- Procedure conformance display (not judgment): lateral distance from the CIFP leg and
  vertical difference from the published altitude constraints, shown as numbers and shading
  with the raw values, never as "deviation" language in generated text.

**Renderer**

- Default stack: MapLibre GL (3D terrain from open Terrarium elevation tiles, no key) +
  deck.gl layers for the trajectory. Base imagery: OpenStreetMap raster or an open vector
  style; FAA VFR sectional raster as an optional overlay (public domain); d-TPP plate warped
  from its embedded georeference where parseable.
- Trajectory layers: path colored by altitude, speed, or vertical rate (user choice); a
  vertical "curtain" from each point to the terrain to make altitude legible; a time cursor
  aircraft glyph; gap styling; optional CIFP procedure legs as 3D ribbons at published
  altitudes; runway polygons from FAA airport data.
- Time control: a scrubber synchronized across the 3D view, the merged timeline, the docket
  ATC transcript (if present), and the summary's cited events (clicking a citation seeks to
  it).
- Camera presets: overview, follow-aircraft, final segment, approach view aligned with the
  runway, top-down.
- Optional alternative renderer: CesiumJS for a true globe with photogrammetry/imagery via
  a user-supplied Cesium ion token (key-gated, not required).
- Export: PNG figures of preset views and an animated replay (MP4/GIF) that contain no
  recoverable point data. No KML/CSV/GeoJSON of positions (D6).

### 4.7 Storage, provenance, export

- One SQLite file per case: `cases/<NTSB-number>.sqlite` (D5), created from the shared
  migrations plus case tables (§5.3). Raw downloads live in the content-addressed cache and
  are referenced by hash.
- Every generated artifact is a version with inputs hash, prompt hash, model id, validator
  log. Re-fetching a report after the NTSB updates it creates a new report version and
  triggers a new summary version; the UI shows a diff.
- Export bundle (zip): summary Markdown + PDF, figures, citation list (CSV/JSON with URLs and
  retrieval dates), evidence map, licence/attribution file (NTSB public domain, FAA public
  domain, ODbL attribution for adsb.lol-derived figures, OSM attribution). No track points.

---

## 5. Architecture

### 5.1 Engine additions

```
liveatc_digest/
  sources/
    ntsb/        case_api.py (api.ntsb.gov) · carol.py · bulk.py (monthly MDB → SQLite) ·
                 docket.py (TOC parse, document fetch, transcript/radar extraction)
    faa/         registry.py (N-number → hex) · ecfr.py · aim.py · ac.py · drs.py (ADs, TCDS) ·
                 dtpp.py · cifp.py · nasr.py (airports/runways/navaids)
    adsb/        base.py (TrackProvider) · adsblol.py · adsbx.py · aeroapi.py · opensky.py ·
                 docket_radar.py
  refs/          corpus.py (download/index) · chunking.py · mapping.yaml · attach.py (3 channels)
  geo/           terrain.py (DEM sampling) · procedures.py (CIFP → 3D legs) · derive.py (kinematics)
  case/          builder.py · summary.py (template + validator extensions) · export.py · watch.py
  viewer/        static web bundle (MapLibre + deck.gl) served by the sidecar, also exportable as
                 a self-contained HTML replay for the CLI
```

### 5.2 Data flow

```
NTSB number ─► case_api/carol ─► case record ─► report PDFs ─► paragraphs ─┐
                                   │                                        ├─► summary (LLM) ─► validator ─► versions
                                   ├─► docket TOC ─► transcripts / radar ───┤
                                   ├─► registry ─► hex ─► TrackProvider ─► Parquet ─► derive ─► timeline + 3D
                                   └─► facts (phase, wx, type, procedure, findings) ─► refs.attach ─► references
                                                                                      └─► chat (RAG, validator)
```

### 5.3 Per-case database additions (on top of the shared schema)

```
ntsb_case         ntsb_number, mode, event_type, report_status, event_local, event_utc, tz_source,
                  city, state, country, lat, lon, latlon_source, nearest_airport, phase_of_flight,
                  flight_rules, operation_part, wx_condition, injury_fatal/serious/minor/none,
                  registration, make, model, series, engine_make, engine_model, icao_hex, hex_source
ntsb_report       id, case, kind (prelim|factual|final), fetched_at, source_fetch_id, text
ntsb_paragraph    id, report_id, ordinal, text                          -- cited as [n:<ntsb>:pNN]
ntsb_finding      id, case, ordinal, category_path, text
ntsb_probable_cause  case, text, issued_at
docket_document   id, case, title, doc_type, url, source_fetch_id, extracted_kind (transcript|radar|other)
track             (shared) + provider in {adsblol, adsbx, aeroapi, opensky, ntsb-docket}
track_event       (shared)
derived_metric    id, track_id, t, kind (agl_ft|dist_to_leg_nm|alt_vs_constraint_ft), value, method
reference         id, case, kind (cfr|aim|ac|ad|tcds|plate|cifp|handbook), citation, title, url,
                  version_label, effective_date, retrieved_at, channel (map|explicit|semantic), excerpt
ref_chunk         id, corpus, citation, as_of_date, text, embedding        -- cited as [r:<id>]
summary_version   (report_version, kind='case')
chat_session / chat_message  (shared)
watch             case, next_check_at, last_status
```

### 5.4 Shared with the digest tool

Utterance model (docket transcripts are utterances), validator, LLM layer, store, cache,
HTTP client, CLI shell, settings and keys, desktop sidecar. The map viewer built here is
reused by the digest tool's Phase 2 replay.

---

## 6. UI specification (desktop app, "Case Studies" area)

Minimal, three-pane layout, system font, one accent color, light/dark.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ Search: [ ERA24LA123 | N123AB 2024-07-28 | "Cessna 172 Massachusetts 2024" ]   │
├──────────────┬─────────────────────────────────────────────┬───────────────────┤
│ Case header  │ 3D trajectory                               │ References        │
│ status badge │ (terrain, track, curtain, procedure legs)   │ ▸ 14 CFR (3)      │
│ gaps list    │ camera presets · layer toggles · color-by   │ ▸ AIM (2)         │
│──────────────│─────────────────────────────────────────────│ ▸ ACs (2)         │
│ Summary      │ ◄──────────── time scrubber ──────────────► │ ▸ ADs (4)         │
│ (cited;      │ 14:03:12Z  alt 2,450 ft  AGL 1,180 ft (der.)│ ▸ Procedure plate │
│ click a      │─────────────────────────────────────────────│ ▸ Handbooks (3)   │
│ citation to  │ Timeline: NTSB ▪  ATC ▪  ADS-B ▪  derived ▪ │ each: citation,   │
│ seek)        │                                             │ excerpt, source,  │
│              │                                             │ version, channel  │
├──────────────┴─────────────────────────────────────────────┴───────────────────┤
│ Chat: "What does AIM 5-4-5 say about the missed approach point?"   [citations] │
└────────────────────────────────────────────────────────────────────────────────┘
```

- Header badges: report status; data coverage (ADS-B %, docket yes/no, transcript yes/no);
  licence note for the figures.
- Summary pane renders the §4.3 template; probable-cause block has a distinct style and the
  NTSB attribution line.
- References pane groups by kind; each item expands to the retrieved excerpt and a "why this
  is here" line naming the channel and the fact that triggered it.
- Chat answers show inline citation chips; a withheld-sentences indicator expands to reasons.
- Export button produces the §4.7 bundle. A "Share" affordance does not exist by design.

---

## 7. CLI specification

```
latc ntsb search  [--number ERA24LA123] [--registration N123AB] [--date 2024-07-28]
                  [--state MA] [--make Cessna] [--model 172] [--status final] [--text "…"] [--json]
latc ntsb show    ERA24LA123                 # case record, report status, docket index
latc ntsb fetch   ERA24LA123 [--docket]      # download reports (+ docket docs) into the cache
latc ntsb import-bulk <avall.mdb|zip>        # build/refresh the offline search index

latc case build   ERA24LA123 [--adsb adsblol|adsbx|aeroapi|none] [--no-llm] [--refs/--no-refs]
latc case show    ERA24LA123 [--summary|--timeline|--references|--gaps|--evidence]
latc case refs    ERA24LA123 [--kind cfr|aim|ac|ad|plate] [--explain]   # shows channel + fact
latc case map     ERA24LA123 --out replay.html                           # self-contained 3D replay
latc case export  ERA24LA123 --out bundle.zip
latc case watch   ERA24LA123 [--off]        # re-fetch when the report status changes
latc case chat    ERA24LA123                # interactive; same grounding rules as the GUI

latc refs sync    [--cfr-parts 61,91,97] [--aim] [--acs] [--handbooks]   # build the local corpus
latc refs search  "missed approach point" [--as-of 2024-07-28]
```

All commands support `--json`; long operations are resumable jobs.

---

## 8. Safeguards (extends ARCHITECTURE §7.4)

- **Citation kinds**: `[n:<ntsb>:pNN]` report paragraph, `[d:<id>]` docket document,
  `[t:<id>]` track event or derived metric, `[r:<id>]` reference chunk, `[u…]`/`[e…]` ATC
  utterance/event. Unknown or missing citations remove the sentence.
- **Verbatim sections**: findings and probable cause must match the report text exactly;
  the UI shows them in quotation style with the NTSB attribution.
- **Speculation lint additions**: "would have survived", "could have saved", "contributed to"
  (outside NTSB quotes), "the pilot decided/chose/intended/believed" unless quoted from the
  report, "error", "mistake", "violation", "should/ought", comparatives to other accidents.
- **Reference statements**: any sentence naming a regulation, AD, AC, AIM paragraph, or
  handbook must cite an `[r:…]` chunk whose citation matches the one named.
- **No compliance judgments**: generated text may state what a regulation says and what the
  NTSB documented; the validator rejects "complied", "violated", "in accordance with",
  "contrary to" unless inside an NTSB quote.
- **Date discipline**: a reference dated after the event (an AD issued later, a CFR amendment
  after the date) is shown with an "issued after this event" flag and is excluded from
  generated text.
- **Privacy**: names of individuals never appear in generated text even if the report prints
  them; the report text itself is shown unaltered.

---

## 9. Data sources and verification status

| Need | Source | Access | Verified from this environment? |
|---|---|---|---|
| Case search/record | NTSB Aviation Case API (`api.ntsb.gov`, data dictionary endpoint per data.gov) | REST, documented | **No** (host blocked here). Verify: endpoint list, auth, rate limits, fields. |
| Case search/record | CAROL `carol-main-public/api/Query/*` | Undocumented JSON | **No**. Community clients (`ntsb`, `accipy` on PyPI) encode the payloads; inspect before relying on it. |
| Bulk | NTSB monthly aviation database (MDB) | Download | **No**. Need a Python MDB reader (`mdbtools`/`access_parser`) or the CSV/JSON export. |
| Dockets | `data.ntsb.gov/Docket/TOCPrintable?data=<id>` | HTML | Format seen in search results: items with title, type (PDF/CSV/unknown). The docket id ↔ NTSB number mapping must be confirmed. |
| Registry | FAA Releasable Aircraft Database (MASTER.txt with Mode S hex) | Zip download | **No** (host blocked). Known format; confirm URL and refresh cadence. |
| ADS-B history | adsb.lol `globe_history_2026` | GitHub releases, ODbL | **Yes**: exists, ~1,214 commits, daily tar split into parts, readsb gzip JSON, `traces/` folder. Per-file layout to confirm on one download. |
| ADS-B history | ADS-B Exchange, AeroAPI, OpenSky | Key / account | Terms known; not exercised. |
| Regulations | eCFR versioner API | REST, no key | **No** (host blocked). Endpoint shapes known from documentation. |
| AIM, ACs, ADs, TCDS, handbooks | faa.gov, DRS | HTML/PDF; DRS has no documented API | **No**. DRS export mechanics to confirm. |
| Procedures | d-TPP metafile + PDFs; CIFP | 28-day cycles | **No** this session (seen earlier: cycle pages exist; older cycles may not be retained). |
| Terrain | AWS Terrarium elevation tiles (Mapzen/Joerd), Mapterhorn | Free, no key | **Yes** (search results confirm URL pattern, encoding `terrarium`, maxzoom 13). |
| Base map | OpenStreetMap raster / open vector tiles | Free with attribution | Known. |

A `scripts/verify_sources.py` that probes each of these and prints what it finds should be
the first build task, run on a machine with normal network access.

---

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| NTSB APIs change or throttle | Bulk database as the always-available fallback; cache everything; versioned source adapters |
| No ADS-B for many GA accidents | Docket radar import; honest coverage header; the tool is still useful without a track |
| Old procedure cycles unavailable | State the gap; show the current plate clearly labelled "current cycle, not the one in effect" only if the user opts in |
| LLM drifts into speculation | Validator is mandatory and logged; red-team prompt set in CI with known-bad outputs that must be removed |
| Over-attachment of references makes everything look causal | Channel labels, "related reading" wording, the curated map is reviewable, references are capped per kind and ranked |
| ODbL / terms | No point export; attribution file in every bundle; adapters off by default for research-only sources |
| 3D performance on laptops | deck.gl handles 10⁵ points easily; decimate for display only, never for storage |

---

## 11. Delivery plan (CLI first, GUI thin)

| Phase | Scope | Done when |
|---|---|---|
| A — Sources | `scripts/verify_sources.py`; ntsb case API/CAROL/bulk adapters; docket TOC + fetch; registry; adsb.lol provider; per-case DB | `latc ntsb search`, `latc ntsb fetch --docket`, `latc case build --no-llm --no-refs` produce a case file with report paragraphs, docket index, and a track (or a stated gap) |
| B — Summary | PDF → paragraphs; summary template; validator extensions; versions and diff | `latc case build` yields a validated summary; red-team suite passes |
| C — References | `latc refs sync`; eCFR point-in-time; AIM/AC/AD/TCDS/handbook ingestion; mapping.yaml; attach channels | `latc case refs --explain` lists references with channel, excerpt, version, date flags |
| D — 3D map | terrain sampling, CIFP → 3D legs, kinematics, viewer bundle, `latc case map` self-contained HTML, PNG export | A case with ADS-B replays in 3D with synchronized timeline in the exported HTML |
| E — Chat + GUI | RAG chat with validator; desktop "Case Studies" area over the sidecar; export bundle; watch | Every CLI capability reachable in the app; chat answers always cited |

Phases A–C are independent of the LiveATC bot-protection issue; the case tool depends on
LiveATC only when docket audio is absent and the event is recent (D1).

---

## 12. Open questions for this workshop

1. **Scope of "accident".** Include NTSB *incidents* and foreign investigations in which the
   NTSB participated, or US accidents/incidents only for v1?
2. **Time range.** The bulk database reaches back to 1962, but ADS-B exists from ~2016 and
   docket radar is spotty earlier. Default search to 2010+ with older cases allowed?
3. **Offline bulk index.** Ship with the NTSB monthly database imported on first run (hundreds
   of MB), or fetch per query only? The spec assumes import on first run for instant browse.
4. **Reference mapping governance.** The curated map is the most opinionated artifact in the
   tool. Who reviews changes, and should the app show the map's version with every case?
5. **Derived conformance display.** Showing lateral/vertical difference from a procedure is
   numeric and labelled, but it is close to judgment. Keep it, make it opt-in, or drop it?
6. **Names in reports.** Reports rarely print names, but dockets do (6120.1 forms). Show
   docket documents unaltered (they are public records) while excluding names from generated
   text, as specified? Or redact in-app?
7. **Cesium.** Worth the optional renderer in v1, or MapLibre/deck.gl only?
8. **Export formats.** PDF summary via a headless browser print, or Markdown + PNG only?
9. **Watch cadence.** Daily poll per watched case, or weekly? NTSB final reports change
   status rarely.
10. **Where the chat LLM runs.** v0 is cloud (OpenAI/Anthropic). Is a fully local option
    required before classroom use, given that case materials are public records anyway?
