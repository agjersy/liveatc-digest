"""Digest generation: a deterministic, fully cited skeleton plus an optional LLM narrative.

The skeleton never needs an LLM and is always included, so a digest is useful offline. The
narrative is requested from the configured LLM with strict citation rules and then passed
through the grounding validator; anything the validator removes is logged with the report.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..llm import LlmProvider
from .validator import ValidationResult, validate_markdown


@dataclass(frozen=True)
class UtteranceView:
    id: int
    start_utc: datetime
    end_utc: datetime
    feed: str  # station identifier
    feed_role: str
    phase: str
    text: str
    normalized_text: str
    confidence: float | None
    callsigns: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)  # {id, type, fields, text}


@dataclass(frozen=True)
class DigestInput:
    icao: str
    start_utc: datetime
    end_utc: datetime
    utterances: list[UtteranceView]


@dataclass
class DigestResult:
    markdown: str
    skeleton_markdown: str
    narrative_markdown: str | None
    validation: ValidationResult | None
    evidence_ids: set[str]
    llm_provider: str | None
    llm_model: str | None
    prompt: str | None


def _ts(dt: datetime) -> str:
    return dt.strftime("%H:%M:%SZ")


def _fmt_event(e: dict[str, Any]) -> str:
    fields = e.get("fields") or {}
    label = e["type"].replace("_", " ")
    detail = ", ".join(f"{k} {v}" for k, v in fields.items() if v not in ("", None))
    return f"{label}" + (f" ({detail})" if detail else "")


def build_skeleton(inp: DigestInput) -> tuple[str, set[str]]:
    ids: set[str] = set()
    lines: list[str] = []
    lines.append(
        f"# ATC digest — {inp.icao.upper()} — {inp.start_utc:%Y-%m-%d %H:%MZ} "
        f"to {inp.end_utc:%H:%MZ}"
    )
    lines.append("")
    feeds = sorted({u.feed for u in inp.utterances})
    lines.append(
        f"> Feeds: {', '.join(feeds) if feeds else 'none'}. Utterances: {len(inp.utterances)}."
    )
    lines.append("")

    by_phase: dict[str, list[UtteranceView]] = defaultdict(list)
    for u in inp.utterances:
        by_phase[u.phase].append(u)

    for phase in ("ground", "terminal", "enroute"):
        us = by_phase.get(phase, [])
        if not us:
            continue
        lines.append(f"## {phase.title()} activity")
        lines.append("")
        aircraft: dict[str, list[UtteranceView]] = defaultdict(list)
        for u in us:
            for cs in u.callsigns or ["(unidentified)"]:
                aircraft[cs].append(u)
        for cs in sorted(aircraft, key=lambda k: (k == "(unidentified)", k)):
            items = aircraft[cs]
            first, last = items[0], items[-1]
            lines.append(
                f"- **{cs}**: {len(items)} transmissions, {_ts(first.start_utc)} to "
                f"{_ts(last.end_utc)} [u{first.id}] [u{last.id}]."
            )
            ids.update({f"u{first.id}", f"u{last.id}"})
            for u in items:
                for e in u.events:
                    lines.append(f"  - {_ts(u.start_utc)} {_fmt_event(e)} [u{u.id}] [e{e['id']}]")
                    ids.update({f"u{u.id}", f"e{e['id']}"})
        lines.append("")

    lines.append("## Chronology")
    lines.append("")
    for u in inp.utterances:
        flag = "" if u.confidence is None or u.confidence >= 0.5 else " (low confidence)"
        who = ", ".join(u.callsigns) if u.callsigns else "—"
        lines.append(f"- {_ts(u.start_utc)} `{u.feed}` {who}: {u.text}{flag} [u{u.id}]")
        ids.add(f"u{u.id}")
    lines.append("")
    return "\n".join(lines), ids


NARRATIVE_SYSTEM = """\
You summarize air traffic control radio transcripts for an aviation-safety education tool.
Rules you must follow exactly:
1. Describe only what the transcript shows. Never infer intent, cause, error, or what anyone
   should have done.
2. Every sentence must end with one or more citations in the form [uNNN] referring to the
   utterance ids given.
3. Do not invent callsigns, runways, altitudes, or facilities that are not in the cited
   utterances.
4. If the transcript is unclear or low confidence, say so rather than guessing.
5. Write short paragraphs grouped under the headings '## Ground', '## Terminal', '## Enroute'
   (omit empty ones), then '## Notable items' as a bullet list.
Output Markdown only."""


def build_narrative_prompt(inp: DigestInput) -> str:
    rows = []
    for u in inp.utterances:
        conf = "" if u.confidence is None else f" conf={u.confidence:.2f}"
        cs = f" callsigns={','.join(u.callsigns)}" if u.callsigns else ""
        rows.append(
            f"[u{u.id}] {_ts(u.start_utc)} {u.feed} ({u.feed_role}, {u.phase}){conf}{cs}: {u.text}"
        )
    window = f"{inp.start_utc:%Y-%m-%d %H:%MZ} to {inp.end_utc:%H:%MZ}"
    header = f"Airport {inp.icao.upper()}, window {window}. Transcript:"
    return header + "\n" + "\n".join(rows)


def build_digest(inp: DigestInput, llm: LlmProvider | None = None) -> DigestResult:
    skeleton, ids = build_skeleton(inp)
    event_ids = {f"e{e['id']}" for u in inp.utterances for e in u.events}
    known = {f"u{u.id}" for u in inp.utterances} | event_ids

    narrative: str | None = None
    validation: ValidationResult | None = None
    prompt: str | None = None
    if llm is not None and inp.utterances:
        prompt = build_narrative_prompt(inp)
        resp = llm.complete(NARRATIVE_SYSTEM, prompt, max_tokens=1500)
        validation = validate_markdown(resp.text, known)
        narrative = validation.markdown
        ids |= validation.citations

    if narrative:
        markdown = (
            skeleton.split("\n## Chronology")[0].rstrip()
            + "\n\n# Narrative\n\n"
            + narrative.strip()
            + "\n\n## Chronology"
            + skeleton.split("\n## Chronology", 1)[1]
        )
    else:
        markdown = skeleton
    return DigestResult(
        markdown=markdown,
        skeleton_markdown=skeleton,
        narrative_markdown=narrative,
        validation=validation,
        evidence_ids=ids,
        llm_provider=llm.name if llm else None,
        llm_model=llm.model if llm else None,
        prompt=prompt,
    )


def evidence_map(inp: DigestInput) -> dict[str, Any]:
    """Serializable map from evidence id to what it points at, stored with every report."""
    out: dict[str, Any] = {}
    for u in inp.utterances:
        out[f"u{u.id}"] = {
            "kind": "utterance",
            "start_utc": u.start_utc.isoformat(),
            "end_utc": u.end_utc.isoformat(),
            "feed": u.feed,
            "text": u.text,
        }
        for e in u.events:
            out[f"e{e['id']}"] = {
                "kind": "event",
                "utterance": f"u{u.id}",
                "type": e["type"],
                "fields": e.get("fields", {}),
            }
    return json.loads(json.dumps(out))
