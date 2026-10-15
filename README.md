# liveatc-digest

Download, transcribe, and reconstruct LiveATC.net archived recordings — with ADS-B track
replay, FAA chart overlays, and a non-speculative NTSB case-study toolkit.

**Status:** early prototype (`ingest.py`) plus a design under discussion.

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design, data sources, safeguards,
  data model, open questions.
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased delivery plan.

## Prototype

`ingest.py` downloads one 30-minute LiveATC archive block for an airport, slices a chunk,
and transcribes it with OpenAI Whisper (cloud) or `openai-whisper` (local).

```
pip install -r requirements.txt
git submodule update --init
python ingest.py
```
