"""Transcription providers behind one interface.

Each provider turns a short WAV file into text with an optional confidence in [0, 1]. The
confidence is derived from the model's average token log-probability where the provider
exposes it, so it is comparable within a provider but not across providers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import Settings, get_api_key


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    confidence: float | None
    model: str


class TranscriptionProvider(Protocol):
    name: str
    model: str

    def transcribe(self, wav: Path, prompt: str | None = None) -> TranscriptSegment: ...


def _conf_from_logprob(
    avg_logprob: float | None, no_speech_prob: float | None = None
) -> float | None:
    if avg_logprob is None:
        return None
    conf = math.exp(avg_logprob)
    if no_speech_prob is not None:
        conf *= 1.0 - no_speech_prob
    return max(0.0, min(1.0, conf))


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str = "whisper-1", api_key: str | None = None, client=None) -> None:
        self.model = model
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key or get_api_key("openai"))
        self.client = client

    def transcribe(self, wav: Path, prompt: str | None = None) -> TranscriptSegment:
        kwargs: dict = {"model": self.model, "language": "en"}
        if prompt:
            kwargs["prompt"] = prompt
        verbose = self.model.startswith("whisper")
        kwargs["response_format"] = "verbose_json" if verbose else "json"
        if not verbose:
            kwargs["include"] = ["logprobs"]
        with wav.open("rb") as fh:
            resp = self.client.audio.transcriptions.create(file=fh, **kwargs)
        text = (getattr(resp, "text", "") or "").strip()
        confidence: float | None = None
        segments = getattr(resp, "segments", None)
        if segments:
            lps = [s.avg_logprob for s in segments if getattr(s, "avg_logprob", None) is not None]
            nsp = [
                s.no_speech_prob for s in segments if getattr(s, "no_speech_prob", None) is not None
            ]
            if lps:
                confidence = _conf_from_logprob(
                    sum(lps) / len(lps), (sum(nsp) / len(nsp)) if nsp else None
                )
        logprobs = getattr(resp, "logprobs", None)
        if confidence is None and logprobs:
            vals = [lp.logprob for lp in logprobs if getattr(lp, "logprob", None) is not None]
            if vals:
                confidence = _conf_from_logprob(sum(vals) / len(vals))
        return TranscriptSegment(text, confidence, self.model)


class LocalWhisperProvider:
    """faster-whisper (CTranslate2). *model* is a size name or a path/HF id of a converted model."""

    name = "local-whisper"

    def __init__(
        self, model: str = "small.en", device: str = "auto", compute_type: str = "default"
    ) -> None:
        from faster_whisper import WhisperModel

        self.model = model
        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, wav: Path, prompt: str | None = None) -> TranscriptSegment:
        segments, _info = self._model.transcribe(
            str(wav), language="en", initial_prompt=prompt, vad_filter=True, beam_size=5
        )
        texts: list[str] = []
        lps: list[float] = []
        nsp: list[float] = []
        for seg in segments:
            texts.append(seg.text.strip())
            lps.append(seg.avg_logprob)
            nsp.append(seg.no_speech_prob)
        text = " ".join(t for t in texts if t)
        conf = _conf_from_logprob(sum(lps) / len(lps), sum(nsp) / len(nsp)) if lps else None
        return TranscriptSegment(text, conf, self.model)


def make_provider(
    settings: Settings, provider: str | None = None, model: str | None = None
) -> TranscriptionProvider:
    name = provider or settings.asr.provider
    chosen = model or settings.asr.model
    if name == "openai":
        return OpenAIProvider(chosen or "whisper-1")
    if name == "local-whisper":
        return LocalWhisperProvider(chosen or "small.en")
    raise ValueError(f"unknown ASR provider: {name}")


def build_domain_prompt(
    icao: str, station_title: str, frequencies: list[dict[str, str]], extra: list[str] | None = None
) -> str:
    """Bias decoding toward ATC vocabulary and the local facility names."""
    parts = [
        f"Air traffic control radio at {icao.upper()}, {station_title}.",
        "Standard ICAO phraseology: cleared for takeoff, cleared to land, line up and wait,",
        "hold short, taxi via, contact tower, squawk, altimeter, runway, niner, tree, fife.",
    ]
    if frequencies:
        parts.append(
            "Facilities: " + ", ".join(f"{f['title']} {f['mhz']}" for f in frequencies) + "."
        )
    if extra:
        parts.append(" ".join(extra))
    return " ".join(parts)
