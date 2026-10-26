# liveatc-digest

Download, transcribe, and reconstruct LiveATC.net archived recordings — with ADS-B track
replay, FAA chart overlays, and a non-speculative NTSB case-study toolkit.

**Status:** Phase 1 (digest MVP). The `latc` CLI lists feeds, fetches archive blocks into a
content-addressed cache, segments and transcribes them (OpenAI or local faster-whisper),
extracts callsigns and clearances with character spans, and writes a fully cited digest whose
LLM narrative is checked by a grounding validator before it is saved as a report version.

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design, data sources, safeguards,
  data model, decisions log.
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased delivery plan.
- [docs/NTSB_CASE_STUDY_SPEC.md](docs/NTSB_CASE_STUDY_SPEC.md) — NTSB case-study tool:
  search, cited summaries, FAA reference retrieval, 3D ADS-B trajectory map, educational chat.

## Layout

```
engine/      Python package `liveatc_digest` + `latc` CLI  (see engine/README.md)
docs/        design documents
scripts/     one-off and legacy scripts
```

## Quick start

```
pip install -e "engine[dev,openai]"      # add asr-local for faster-whisper
latc config set-key openai                # stored in the OS keychain
latc stations KBED
latc transcribe kbed_twr --from 2026-10-14T18:00Z --to 2026-10-14T19:00Z
latc transcript KBED --from 2026-10-14T18:00Z --to 2026-10-14T19:00Z
latc digest KBED --from 2026-10-14T18:00Z --to 2026-10-14T19:00Z --out digest.md
latc report list
```

Every sentence of a digest cites the utterance or event it rests on (`[u12]`, `[e3]`); the
validator removes anything uncited or speculative and logs what it removed with the report.
Use `--no-llm` for a deterministic digest without any API calls.

LiveATC keeps archives for about 30 days; `latc fetch` reports blocks outside that window
as `expired` without contacting the server. Everything is stored under the platform user
data directory, or under `$LIVEATC_DIGEST_HOME` when set.

## If `latc stations` reports a human-verification page

LiveATC's HTML pages (the airport search and the per-feed archive page) sit behind
bot protection that answers automated requests with a human-verification challenge. The tool
detects this and does **not** try to get around it. The recordings themselves live on
`archive.liveatc.net` and are fetched directly, so you only need to tell the tool which feed
and archive ID to use:

```
# 1. Register the feed once by hand. The archive ID is the filename prefix of any recording
#    listed on the feed's archive page, e.g. KBED-Twr for KBED-Twr-Oct-23-2026-1800Z.mp3.
latc station add kbed_twr --icao KBED --archive-id KBED-Twr --role tower --freq 118.5:Tower

# 2. ...or import the search page you opened in your own browser (File > Save Page As).
latc stations KBED --from-html ~/Downloads/kbed.html

# 3. ...or pass the archive ID straight to fetch/transcribe.
latc transcribe kbed_twr --from 2026-10-23T18:00Z --to 2026-10-23T19:00Z --archive-id KBED-Twr
```

Once a feed is registered, `fetch`, `transcribe`, and `digest` never touch the protected pages.

## If the recording download itself returns HTTP 403

Check what the archive host is actually sending back:

```
latc diagnose https://archive.liveatc.net/kbed/KBED-Twr-Oct-24-2026-0000Z.mp3
```

If it reports a human-verification page, or LiveATC refuses non-browser downloads, download the
file in your browser from the feed's archive page and import it. Everything downstream then
runs offline:

```
latc import ~/Downloads/KBED-Twr-Oct-24-2026-0000Z.mp3 --station kbed_twr
latc transcribe kbed_twr --from 2026-10-24T00:00Z --to 2026-10-24T00:30Z
```

The archive ID and block start are read from the filename; pass `--archive-id` and `--start`
if you renamed the file. Please review LiveATC's terms of use before automating any access.
