"""Original prototype, kept for reference. Downloads one LiveATC block and transcribes a slice.

Prefer the `latc` CLI (engine/) for fetching; this script will be removed once Phase 1
adds `latc transcribe`.

Usage:  pip install -e "engine[audio,asr-openai]"  &&  python scripts/legacy_ingest.py
"""

from datetime import UTC, datetime

from pydub import AudioSegment

from liveatc_digest.cache import Cache
from liveatc_digest.config import load_settings
from liveatc_digest.http import HttpClient
from liveatc_digest.sources import liveatc
from liveatc_digest.store import Store

if __name__ == "__main__":
    settings = load_settings()
    client = HttpClient(settings.liveatc.user_agent)
    cache = Cache(settings.cache_dir)
    store = Store(settings.workspace_db)

    icao = "KBED"
    station = next(s for s in liveatc.fetch_stations(client, icao) if s.role == "tower")
    archive_id = liveatc.resolve_archive_id(client, station.identifier)
    block = liveatc.Block(datetime(2024, 7, 28, 0, 0, tzinfo=UTC))
    fetched = liveatc.fetch_block(client, cache, store, icao, archive_id, block)
    print(fetched.path)

    recording = AudioSegment.from_mp3(fetched.path)
    ten_minutes = 10 * 60 * 1000
    chunkpath = cache.temp_path(".mp3")
    recording[ten_minutes : 2 * ten_minutes].export(chunkpath, format="mp3")

    print("Transcribing audio...")
    use_api = True
    if use_api:
        from openai import OpenAI

        with open(chunkpath, "rb") as audio_file:
            transcription = OpenAI().audio.transcriptions.create(model="whisper-1", file=audio_file)
        print(transcription.text)
    else:
        import whisper

        model = whisper.load_model("small.en")
        print(model.transcribe(str(fetched.path), fp16=False, language="English")["text"])
