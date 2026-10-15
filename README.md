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
