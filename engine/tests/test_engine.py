import json
from datetime import UTC, datetime

from liveatc_digest.audio import TranscriptSegment
from liveatc_digest.cache import Cache
from liveatc_digest.config import Settings
from liveatc_digest.engine import Engine
from liveatc_digest.http import HttpClient
from liveatc_digest.llm import LlmResponse
from liveatc_digest.store import Store
from tests.audio_fixtures import write_bursts
from tests.conftest import FakeResponse

START = datetime(2024, 7, 28, 0, 0, tzinfo=UTC)
END = datetime(2024, 7, 28, 0, 30, tzinfo=UTC)
URL = "https://archive.liveatc.net/kbed/KBED-Twr-Jul-28-2024-0000Z.mp3"


class FakeAsr:
    name = "fake-asr"
    model = "v1"
    texts = [
        "Cessna one two three alpha bravo runway two nine cleared to land",
        "Taxi via alpha hold short runway two nine",
    ]

    def __init__(self) -> None:
        self.calls = 0
        self.prompts = []

    def transcribe(self, wav, prompt=None):
        self.prompts.append(prompt)
        text = self.texts[self.calls % len(self.texts)]
        self.calls += 1
        return TranscriptSegment(text, 0.7, self.model)


class FakeLlm:
    name = "fake-llm"
    model = "m"

    def complete(self, system, user, json_mode=False, max_tokens=2048):
        return LlmResponse(
            "## Terminal\n\nA landing clearance was issued [u1]. Uncited claim.\n",
            self.name,
            self.model,
        )


def make_engine(tmp_path, session, search_html, archive_html, asr, llm=None, now=None):
    settings = Settings(cache_dir=tmp_path / "cache", workspace_db=tmp_path / "ws.sqlite")
    session.add(
        "https://www.liveatc.net/search/?icao=kbed", FakeResponse(200, search_html.encode())
    )
    session.add(
        "https://www.liveatc.net/archive.php?m=kbed_twr", FakeResponse(200, archive_html.encode())
    )
    wav = write_bursts(tmp_path / "block.wav", [(1.0, 2.5), (4.0, 5.0)], total_s=6.0)
    session.add(URL, FakeResponse(200, wav.read_bytes(), "audio/mpeg"))
    msgs = []
    eng = Engine(
        settings,
        store=Store(settings.workspace_db),
        cache=Cache(settings.cache_dir),
        client=HttpClient("t", session=session, sleep=lambda s: None),
        asr_factory=lambda *_a, **_k: asr,
        llm_factory=lambda *_a, **_k: llm,
        progress=msgs.append,
    )
    return eng, msgs


def test_transcribe_stores_utterances_with_absolute_times(
    tmp_path, session, search_html, archive_html, monkeypatch
):
    monkeypatch.setattr("liveatc_digest.sources.liveatc.archive.datetime", _FrozenDatetime)
    asr = FakeAsr()
    eng, msgs = make_engine(tmp_path, session, search_html, archive_html, asr)
    outcomes = eng.transcribe("kbed_twr", START, END)
    assert len(outcomes) == 1 and outcomes[0].utterances == 2 and not outcomes[0].skipped
    assert asr.calls == 2 and "KBED" in asr.prompts[0] and "Hanscom Tower" in asr.prompts[0]

    views = eng.utterance_views("KBED", START, END)
    assert [v.id for v in views] == [1, 2]
    assert abs((views[0].start_utc - START).total_seconds() - 1.0) < 0.3
    assert views[0].callsigns == ["N123AB"]
    assert views[0].events[0]["type"] == "landing_clearance" and views[0].phase == "terminal"
    assert views[1].phase == "ground"

    # second run is a no-op unless forced
    again = eng.transcribe("kbed_twr", START, END)
    assert again[0].skipped and asr.calls == 2
    forced = eng.transcribe("kbed_twr", START, END, force=True)
    assert not forced[0].skipped and asr.calls == 4
    assert len(eng.utterance_views("KBED", START, END)) == 2

    saved = eng.digest("KBED", START, END, use_llm=False)
    assert saved.version == 1 and saved.utterance_count == 2
    row = eng.store.get_report(saved.reconstruction_id)
    assert "## Chronology" in row["body_md"]
    ev = json.loads(row["evidence_map_json"])
    current = eng.utterance_views("KBED", START, END)
    assert all(ev[f"u{v.id}"]["kind"] == "utterance" for v in current)


def test_digest_with_llm_records_validator_log(
    tmp_path, session, search_html, archive_html, monkeypatch
):
    monkeypatch.setattr("liveatc_digest.sources.liveatc.archive.datetime", _FrozenDatetime)
    eng, _ = make_engine(tmp_path, session, search_html, archive_html, FakeAsr(), llm=FakeLlm())
    eng.transcribe("kbed_twr", START, END)
    saved = eng.digest("KBED", START, END, use_llm=True)
    assert saved.result.llm_provider == "fake-llm"
    row = eng.store.get_report(saved.reconstruction_id)
    log = json.loads(row["validator_log_json"])
    assert len(log) == 1 and log[0]["reason"] == "no citation"
    assert "A landing clearance was issued [u1]." in row["body_md"]
    assert row["prompt_hash"] and row["inputs_hash"]
    # every digest run creates a new reconstruction at version 1
    saved2 = eng.digest("KBED", START, END, use_llm=False)
    assert saved2.reconstruction_id != saved.reconstruction_id
    assert len(eng.store.list_reports()) == 2


class _FrozenDatetime(datetime):
    """datetime whose now() sits one day after the test block so retention checks pass."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2024, 7, 29, 0, 0, tzinfo=UTC)


def test_manual_station_avoids_html_pages(tmp_path, session, monkeypatch):
    """With --archive-id or `station add`, no search/archive page is ever requested."""
    monkeypatch.setattr("liveatc_digest.sources.liveatc.archive.datetime", _FrozenDatetime)
    settings = Settings(cache_dir=tmp_path / "cache", workspace_db=tmp_path / "ws.sqlite")
    wav = write_bursts(tmp_path / "block.wav", [(1.0, 2.5)], total_s=4.0)
    session.add(URL, FakeResponse(200, wav.read_bytes(), "audio/mpeg"))
    eng = Engine(
        settings,
        store=Store(settings.workspace_db),
        cache=Cache(settings.cache_dir),
        client=HttpClient("t", session=session, sleep=lambda s: None),
        asr_factory=lambda *_a, **_k: FakeAsr(),
    )
    outcomes = eng.fetch("kbed_twr", START, END, archive_id="KBED-Twr")
    assert outcomes[0].status == "fetched"
    assert session.calls == [URL]
    row = eng.store.get_station("kbed_twr")
    assert row["archive_id"] == "KBED-Twr" and row["role"] == "tower" and row["icao"] == "KBED"


def test_challenge_gives_guidance(tmp_path, session):
    from liveatc_digest.engine import StationUnavailable

    settings = Settings(cache_dir=tmp_path / "cache", workspace_db=tmp_path / "ws.sqlite")
    body = b"<html><title>Just a moment...</title>verify you are human</html>"
    session.add("https://www.liveatc.net/search/?icao=kbed", FakeResponse(403, body))
    eng = Engine(
        settings,
        store=Store(settings.workspace_db),
        cache=Cache(settings.cache_dir),
        client=HttpClient("t", session=session, sleep=lambda s: None),
    )
    try:
        eng.stations("KBED")
    except StationUnavailable as exc:
        msg = str(exc)
    else:
        raise AssertionError("expected StationUnavailable")
    assert "latc station add kbed_twr --icao KBED" in msg and "--from-html" in msg


def test_stations_from_saved_html(tmp_path, session, search_html):
    settings = Settings(cache_dir=tmp_path / "cache", workspace_db=tmp_path / "ws.sqlite")
    eng = Engine(
        settings,
        store=Store(settings.workspace_db),
        cache=Cache(settings.cache_dir),
        client=HttpClient("t", session=session, sleep=lambda s: None),
    )
    found = eng.stations("KBED", html=search_html)
    assert [s.identifier for s in found] == ["kbed_twr", "kbed_app"]
    assert session.calls == []
