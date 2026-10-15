"""Callsign extraction from normalized ATC text.

Recognizes N-numbers (``n123ab``, ``cessna 123ab``), airline telephony plus flight number
(``jetblue 1234`` -> ``JBU1234``), and abbreviated N-number tails (``3ab``) with lower
confidence. Every mention carries a character span into the normalized text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources

from .normalize import normalize_text

_N_NUMBER_RE = re.compile(r"\bn(\d{1,5}[a-z]{0,2})\b")
_TYPE_WORDS = (
    "cessna|piper|cirrus|beech|beechcraft|bonanza|baron|mooney|diamond|skyhawk|skylane|"
    "archer|warrior|cherokee|citation|gulfstream|falcon|learjet|lear|hawker|king air|"
    "caravan|navajo|seneca|saratoga|malibu|pilatus|tbm|phenom|challenger|global|"
    "helicopter|robinson|bell|experimental|lifeguard|medevac"
)
_TYPE_RE = re.compile(rf"\b({_TYPE_WORDS}) (?=\w{{2}})(\d{{1,5}}[a-z]{{0,2}})\b(?! o'?clock)")
_SHORT_TAIL_RE = re.compile(r"\b(\d{1,3}[a-z]{1,2})\b")


@dataclass(frozen=True)
class CallsignMention:
    raw: str
    callsign: str
    kind: str  # n_number | airline | partial
    span: tuple[int, int]
    confidence: float


def _load_telephony() -> dict[str, str]:
    path = resources.files("liveatc_digest.nlp") / "telephony.json"
    return json.loads(path.read_text(encoding="utf-8"))


TELEPHONY: dict[str, str] = _load_telephony()
_AIRLINE_RE = re.compile(
    r"\b("
    + "|".join(re.escape(k) for k in sorted(TELEPHONY, key=len, reverse=True))
    + r") (\d{1,4}[a-z]?)\b"
)


def extract_callsigns(text: str, normalized: str | None = None) -> list[CallsignMention]:
    norm = normalized if normalized is not None else normalize_text(text)
    found: list[CallsignMention] = []
    taken: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for m in _N_NUMBER_RE.finditer(norm):
        span = m.span()
        found.append(CallsignMention(m.group(0), "N" + m.group(1).upper(), "n_number", span, 0.9))
        taken.append(span)
    for m in _TYPE_RE.finditer(norm):
        span = m.span()
        if free(span):
            found.append(
                CallsignMention(m.group(0), "N" + m.group(2).upper(), "n_number", span, 0.8)
            )
            taken.append(span)
    for m in _AIRLINE_RE.finditer(norm):
        span = m.span()
        if free(span):
            icao = TELEPHONY[m.group(1)]
            found.append(
                CallsignMention(m.group(0), icao + m.group(2).upper(), "airline", span, 0.85)
            )
            taken.append(span)
    for m in _SHORT_TAIL_RE.finditer(norm):
        span = m.span()
        if free(span):
            found.append(CallsignMention(m.group(0), m.group(1).upper(), "partial", span, 0.4))
            taken.append(span)
    found.sort(key=lambda c: c.span[0])
    return found
