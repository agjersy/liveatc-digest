"""Rule-based extraction of typed ATC events from normalized utterance text.

Every event carries a character span into the normalized text so that nothing is asserted
that was not transcribed. Fields are parsed from the matched span only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .normalize import normalize_text

RUNWAY = r"(?:runway |rwy )?(\d{1,2}[lrc]?)"
FREQ = r"(\d{3}\.\d{1,3})"


@dataclass(frozen=True)
class AtcEvent:
    type: str
    span: tuple[int, int]
    text: str
    fields: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.8


# (type, regex, field builder, confidence). Order matters: earlier wins on overlap.
_PATTERNS: list[tuple[str, re.Pattern[str], Any, float]] = [
    (
        "emergency",
        re.compile(r"\b(mayday|pan pan|declaring an emergency|emergency)\b"),
        lambda m: {"phrase": m.group(1)},
        0.9,
    ),
    ("go_around", re.compile(r"\b(go around|going around)\b"), lambda m: {}, 0.9),
    (
        "takeoff_clearance",
        re.compile(rf"\b{RUNWAY}[, ]*cleared for takeoff\b|\bcleared for takeoff[, ]*{RUNWAY}\b"),
        lambda m: {"runway": (m.group(1) or m.group(2) or "").upper()},
        0.95,
    ),
    (
        "landing_clearance",
        re.compile(rf"\b{RUNWAY}[, ]*cleared to land\b|\bcleared to land[, ]*{RUNWAY}\b"),
        lambda m: {"runway": (m.group(1) or m.group(2) or "").upper()},
        0.95,
    ),
    (
        "option_clearance",
        re.compile(
            rf"\b{RUNWAY}[, ]*cleared for the option\b|\bcleared for the option[, ]*{RUNWAY}\b"
        ),
        lambda m: {"runway": (m.group(1) or m.group(2) or "").upper()},
        0.9,
    ),
    (
        "line_up_and_wait",
        re.compile(rf"\b{RUNWAY}[, ]*line up and wait\b|\bline up and wait[, ]*{RUNWAY}\b"),
        lambda m: {"runway": (m.group(1) or m.group(2) or "").upper()},
        0.9,
    ),
    (
        "hold_short",
        re.compile(rf"\bhold short(?: of)? {RUNWAY}\b"),
        lambda m: {"runway": m.group(1).upper()},
        0.9,
    ),
    (
        "cross_runway",
        re.compile(rf"\bcross {RUNWAY}\b"),
        lambda m: {"runway": m.group(1).upper()},
        0.85,
    ),
    (
        "approach_clearance",
        re.compile(
            r"\bcleared (ils|rnav|gps|vor|visual|localizer|lpv|rnp|ndb)(?: [a-z])? (?:approach )?(?:runway )?(\d{1,2}[lrc]?)?"
        ),
        lambda m: {"approach": m.group(1).upper(), "runway": (m.group(2) or "").upper()},
        0.9,
    ),
    (
        "taxi",
        re.compile(r"\btaxi (?:to|via) (.+?)(?= hold short| cross | contact |[,.]|$)"),
        lambda m: {"route": m.group(1)},
        0.8,
    ),
    (
        "altitude",
        re.compile(
            r"\b(climb and maintain|descend and maintain|maintain|climb via|descend via|descend to|climb to) (fl\d{3}|\d{3,5})\b"
        ),
        lambda m: {"instruction": m.group(1), "altitude": m.group(2).upper()},
        0.85,
    ),
    (
        "heading",
        re.compile(r"\b(turn left|turn right|fly) heading (\d{3})\b"),
        lambda m: {"instruction": m.group(1), "heading": int(m.group(2))},
        0.9,
    ),
    (
        "speed",
        re.compile(r"\b(reduce speed to|increase speed to|maintain) (\d{2,3}) knots\b"),
        lambda m: {"instruction": m.group(1), "knots": int(m.group(2))},
        0.85,
    ),
    (
        "frequency_change",
        re.compile(rf"\bcontact ([a-z ]+?)(?: on)? {FREQ}\b"),
        lambda m: {"facility": m.group(1).strip(), "mhz": m.group(2)},
        0.9,
    ),
    ("squawk", re.compile(r"\bsquawk (\d{4})\b"), lambda m: {"code": m.group(1)}, 0.9),
    (
        "traffic_advisory",
        re.compile(r"\btraffic\b[^.]{0,40}?\b(\d{1,2}) o'?clock\b"),
        lambda m: {"clock": int(m.group(1))},
        0.8,
    ),
    (
        "position_report",
        re.compile(
            r"\b(\d{1,2}) miles? (north|south|east|west|northeast|northwest|southeast|southwest)\b"
        ),
        lambda m: {"distance_nm": int(m.group(1)), "direction": m.group(2)},
        0.6,
    ),
    ("altimeter", re.compile(r"\baltimeter (\d{4})\b"), lambda m: {"setting": m.group(1)}, 0.9),
    (
        "wind",
        re.compile(r"\bwind (\d{3}) at (\d{1,2})\b"),
        lambda m: {"direction": int(m.group(1)), "knots": int(m.group(2))},
        0.9,
    ),
]


def extract_events(text: str, normalized: str | None = None) -> list[AtcEvent]:
    norm = normalized if normalized is not None else normalize_text(text)
    events: list[AtcEvent] = []
    taken: list[tuple[int, int]] = []
    for etype, pattern, build, conf in _PATTERNS:
        for m in pattern.finditer(norm):
            span = m.span()
            if any(not (span[1] <= a or span[0] >= b) for a, b in taken):
                continue
            events.append(AtcEvent(etype, span, m.group(0), build(m), conf))
            taken.append(span)
    events.sort(key=lambda e: e.span[0])
    return events
