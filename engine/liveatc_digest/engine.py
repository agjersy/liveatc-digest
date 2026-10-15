"""Engine facade: the one object the CLI and the desktop sidecar talk to."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config
from .audio import (
    TranscriptionProvider,
    build_domain_prompt,
    make_provider,
    segment_audio,
)
from .cache import Cache
from .http import HttpClient
from .llm import LlmProvider, make_llm
from .nlp import classify_phase, extract_callsigns, extract_events, normalize_text
from .report import DigestInput, DigestResult, UtteranceView, build_digest
from .report.digest import evidence_map
from .sources import liveatc
from .store import Store

Progress = Callable[[str], None]


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_ms(dt: datetime) -> str:
    """ISO-8601 with millisecond precision, for utterance boundaries inside a block."""
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


@dataclass
class BlockOutcome:
    start_utc: datetime
    status: str  # fetched | missing | expired | future
    audio_block_id: int
    path: Path | None = None
    sha256: str | None = None
    cached: bool = False
    reason: str | None = None
    url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {
            "start_utc": iso(self.start_utc),
            "status": self.status,
            "audio_block_id": self.audio_block_id,
        }
        for k in ("sha256", "cached", "reason", "url"):
            v = getattr(self, k)
            if v not in (None, False):
                d[k] = v
        if self.path:
            d["path"] = str(self.path)
        return d


@dataclass
class TranscribeOutcome:
    block: BlockOutcome
    utterances: int
    skipped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {**self.block.as_dict(), "utterances": self.utterances, "skipped": self.skipped}


@dataclass
class SavedDigest:
    reconstruction_id: int
    report_version_id: int
    version: int
    result: DigestResult
    utterance_count: int


class Engine:
    def __init__(
        self,
        settings: config.Settings | None = None,
        store: Store | None = None,
        cache: Cache | None = None,
        client: HttpClient | None = None,
        asr_factory: Callable[..., TranscriptionProvider] | None = None,
        llm_factory: Callable[..., LlmProvider] | None = None,
        progress: Progress | None = None,
    ) -> None:
        self.settings = settings or config.load_settings()
        self._store = store
        self._cache = cache
        self._client = client
        self._asr_factory = asr_factory or make_provider
        self._llm_factory = llm_factory or make_llm
        self.progress = progress or (lambda _msg: None)

    # -- lazy resources ---------------------------------------------------------------------

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.settings.workspace_db)
        return self._store

    @property
    def cache(self) -> Cache:
        if self._cache is None:
            self._cache = Cache(self.settings.cache_dir)
        return self._cache

    @property
    def client(self) -> HttpClient:
        if self._client is None:
            self._client = HttpClient(
                self.settings.liveatc.user_agent, timeout_s=self.settings.liveatc.request_timeout_s
            )
        return self._client

    # -- stations ---------------------------------------------------------------------------

    def stations(self, icao: str) -> list[liveatc.Station]:
        found = liveatc.fetch_stations(self.client, icao)
        for st in found:
            self.store.upsert_station(
                st.icao,
                st.identifier,
                st.title,
                st.role,
                st.up,
                [f.as_dict() for f in st.frequencies],
            )
        self.store.audit("stations.list", icao.upper(), count=len(found))
        return found

    def _station_row(self, mount: str, icao: str):
        row = self.store.get_station(mount)
        if row is None:
            self.stations(icao)
            row = self.store.get_station(mount)
        if row is None:
            raise LookupError(f"unknown station {mount!r} for {icao.upper()}")
        if not row["archive_id"]:
            archive_id = liveatc.resolve_archive_id(self.client, mount)
            self.store.set_station_archive_id(mount, archive_id)
            row = self.store.get_station(mount)
        return row

    @staticmethod
    def icao_for(mount: str, icao: str | None) -> str:
        return (icao or mount.split("_")[0]).upper()

    # -- fetch ------------------------------------------------------------------------------

    def fetch(
        self, mount: str, start: datetime, end: datetime, icao: str | None = None
    ) -> list[BlockOutcome]:
        airport = self.icao_for(mount, icao)
        row = self._station_row(mount, airport)
        retention = self.settings.liveatc.retention_days
        outcomes: list[BlockOutcome] = []
        for block in liveatc.plan_blocks(start, end):
            s, e = iso(block.start_utc), iso(block.end_utc)
            try:
                fetched = liveatc.fetch_block(
                    self.client,
                    self.cache,
                    self.store,
                    airport,
                    row["archive_id"],
                    block,
                    retention_days=retention,
                )
            except liveatc.ArchiveNotAvailable as exc:
                status = str(liveatc.block_status(block, retention_days=retention))
                if status == "available":
                    status = "missing"
                bid = self.store.upsert_audio_block(row["id"], airport, s, e, status)
                outcomes.append(
                    BlockOutcome(block.start_utc, status, bid, reason=exc.reason, url=exc.url)
                )
                self.progress(f"{s} {status}: {exc.reason}")
                continue
            rec = self.store.find_fetch(fetched.url, liveatc.archive.PROVIDER)
            bid = self.store.upsert_audio_block(
                row["id"], airport, s, e, "fetched", rec.id if rec else None
            )
            outcomes.append(
                BlockOutcome(
                    block.start_utc,
                    "fetched",
                    bid,
                    fetched.path,
                    fetched.sha256,
                    fetched.cached,
                    url=fetched.url,
                )
            )
            self.progress(f"{s} fetched{' (cache)' if fetched.cached else ''}")
        self.store.audit("fetch.blocks", mount, start=iso(start), end=iso(end), count=len(outcomes))
        return outcomes

    # -- transcribe -------------------------------------------------------------------------

    def transcribe(
        self,
        mount: str,
        start: datetime,
        end: datetime,
        icao: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        force: bool = False,
        segment_kwargs: dict[str, Any] | None = None,
    ) -> list[TranscribeOutcome]:
        airport = self.icao_for(mount, icao)
        row = self._station_row(mount, airport)
        asr = self._asr_factory(self.settings, provider, model)
        prompt = build_domain_prompt(airport, row["title"], json.loads(row["frequencies_json"]))
        feed_role = row["role"]
        outcomes: list[TranscribeOutcome] = []

        for block in self.fetch(mount, start, end, airport):
            if block.status != "fetched" or block.path is None:
                outcomes.append(TranscribeOutcome(block, 0))
                continue
            existing = self.store.utterance_count_for_block(
                block.audio_block_id, asr.name, asr.model
            )
            if existing and not force:
                outcomes.append(TranscribeOutcome(block, existing, skipped=True))
                self.progress(f"{iso(block.start_utc)} already transcribed ({existing} utterances)")
                continue
            if existing:
                self.store.delete_utterances_for_block(block.audio_block_id)

            work = self.cache.temp_path("-segments")
            try:
                segments = segment_audio(block.path, work, **(segment_kwargs or {}))
                self.progress(f"{iso(block.start_utc)} {len(segments)} speech segments")
                count = 0
                for seg in segments:
                    result = asr.transcribe(seg.path, prompt)
                    text = result.text.strip()
                    if not text:
                        continue
                    count += 1
                    self._store_utterance(
                        block, seg.start_ms, seg.end_ms, text, result.confidence, asr, feed_role
                    )
            finally:
                shutil.rmtree(work, ignore_errors=True)
            self.store.audit(
                "transcribe.block",
                mount,
                block=iso(block.start_utc),
                provider=asr.name,
                model=asr.model,
                utterances=count,
            )
            outcomes.append(TranscribeOutcome(block, count))
            self.progress(f"{iso(block.start_utc)} {count} utterances")
        return outcomes

    def _store_utterance(
        self,
        block: BlockOutcome,
        start_ms: int,
        end_ms: int,
        text: str,
        confidence,
        asr,
        feed_role: str,
    ) -> int:
        from datetime import timedelta

        start_utc = block.start_utc + timedelta(milliseconds=start_ms)
        end_utc = block.start_utc + timedelta(milliseconds=end_ms)
        norm = normalize_text(text)
        callsigns = extract_callsigns(text, norm)
        events = extract_events(text, norm)
        phase = classify_phase(feed_role, events)
        uid = self.store.insert_utterance(
            block.audio_block_id,
            iso_ms(start_utc),
            iso_ms(end_utc),
            text,
            norm,
            asr.name,
            asr.model,
            confidence,
            str(phase),
            feed_role,
        )
        self.store.replace_extractions(uid, callsigns, events)
        return uid

    def reextract(
        self, icao: str, start: datetime, end: datetime, stations: list[str] | None = None
    ) -> int:
        """Re-run rule extraction over stored utterances (after a rules update)."""
        rows = self.store.utterances_in_window(icao, iso_ms(start), iso_ms(end), stations)
        for r in rows:
            norm = normalize_text(r["text"])
            cs = extract_callsigns(r["text"], norm)
            ev = extract_events(r["text"], norm)
            self.store.replace_extractions(r["id"], cs, ev)
            self.store.set_utterance_phase(
                r["id"], str(classify_phase(r["feed_role"] or r["station_role"], ev))
            )
        return len(rows)

    # -- digest -----------------------------------------------------------------------------

    def utterance_views(
        self, icao: str, start: datetime, end: datetime, stations: list[str] | None = None
    ) -> list[UtteranceView]:
        rows = self.store.utterances_in_window(icao, iso_ms(start), iso_ms(end), stations)
        views: list[UtteranceView] = []
        for r in rows:
            views.append(
                UtteranceView(
                    id=r["id"],
                    start_utc=parse_iso(r["start_utc"]),
                    end_utc=parse_iso(r["end_utc"]),
                    feed=r["feed"],
                    feed_role=r["feed_role"] or r["station_role"],
                    phase=r["phase"] or "terminal",
                    text=r["text"],
                    normalized_text=r["normalized_text"] or "",
                    confidence=r["confidence"],
                    callsigns=self.store.callsigns_for(r["id"]),
                    events=self.store.events_for(r["id"]),
                )
            )
        return views

    def digest(
        self,
        icao: str,
        start: datetime,
        end: datetime,
        stations: list[str] | None = None,
        use_llm: bool = True,
        llm_provider: str | None = None,
        llm_model: str | None = None,
    ) -> SavedDigest:
        views = self.utterance_views(icao, start, end, stations)
        inp = DigestInput(icao.upper(), start.astimezone(UTC), end.astimezone(UTC), views)
        llm = (
            self._llm_factory(self.settings, llm_provider, llm_model)
            if (use_llm and views)
            else None
        )
        result = build_digest(inp, llm)
        if not views:
            # Nothing to cite: return the (empty) digest without persisting a report version.
            return SavedDigest(0, 0, 0, result, 0)

        params = {
            "stations": stations,
            "use_llm": use_llm,
            "llm_provider": result.llm_provider,
            "llm_model": result.llm_model,
        }
        rid = self.store.create_reconstruction("digest", icao.upper(), iso(start), iso(end), params)
        inputs_hash = hashlib.sha256(
            json.dumps([[v.id, v.text, v.confidence] for v in views]).encode()
        ).hexdigest()
        prompt_hash = hashlib.sha256(result.prompt.encode()).hexdigest() if result.prompt else None
        log = result.validation.removed if result.validation else []
        vid, version = self.store.add_report_version(
            rid,
            result.markdown,
            evidence_map(inp),
            inputs_hash,
            log,
            result.llm_provider,
            result.llm_model,
            prompt_hash,
        )
        self.store.audit(
            "digest.create",
            f"reconstruction:{rid}",
            version=version,
            utterances=len(views),
            removed=len(log),
        )
        return SavedDigest(rid, vid, version, result, len(views))
