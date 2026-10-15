# liveatc-digest

Download, transcribe, and reconstruct LiveATC.net archived recordings — with ADS-B track
replay, FAA chart overlays, and a non-speculative NTSB case-study toolkit.

**Status:** Phase 0 (foundation). The `latc` CLI can list feeds and fetch archive blocks
into a content-addressed cache with provenance recorded in SQLite. Transcription and
digests arrive in Phase 1.

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design, data sources, safeguards,
  data model, decisions log.
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased delivery plan.

## Layout

```
engine/      Python package `liveatc_digest` + `latc` CLI  (see engine/README.md)
docs/        design documents
scripts/     one-off and legacy scripts
```

## Quick start

```
pip install -e "engine[dev]"
latc stations KBED
latc fetch kbed_twr --from 2026-10-14T18:00Z --to 2026-10-14T19:00Z
latc db info
latc config set-key openai        # stored in the OS keychain
```

LiveATC keeps archives for about 30 days; `latc fetch` reports blocks outside that window
as `expired` without contacting the server. Everything is stored under the platform user
data directory, or under `$LIVEATC_DIGEST_HOME` when set.
