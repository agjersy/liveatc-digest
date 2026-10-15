"""Grounding validator for generated text.

Every sentence of body text must cite at least one known evidence id in the form ``[u123]``
(utterance), ``[e45]`` (event), ``[t7]`` (track) or ``[n:ERA24LA123]`` (NTSB field). Sentences
that fail, or that contain speculative / evaluative language outside a verbatim quote, are
removed and logged so the output never carries unsupported claims. Headings, tables, and
blockquotes (used for metadata lines) pass through unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CITATION_RE = re.compile(r"\[(u|e|t|n):?([A-Za-z0-9]+)\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[\"'])")

DEFAULT_SPECULATION_PATTERNS: tuple[str, ...] = (
    r"\bshould have\b",
    r"\bshould not have\b",
    r"\bcould have (?:been )?avoided\b",
    r"\bcould have prevented\b",
    r"\bwould have\b",
    r"\bif the (?:pilot|crew|controller) had\b",
    r"\bfailed to\b",
    r"\bpilot error\b",
    r"\bnegligen",
    r"\bcareless",
    r"\breckless",
    r"\bblame\b",
    r"\bat fault\b",
    r"\bprobably caused\b",
    r"\blikely caused\b",
    r"\bthe cause (?:was|is)\b",
    r"\bcaused by\b",
    r"\bmistake\b",
    r"\bpoor (?:decision|judgment|airmanship)\b",
    r"\bI (?:think|believe|suspect)\b",
    r"\bit (?:seems|appears) (?:that )?the pilot\b",
)


@dataclass
class ValidationResult:
    markdown: str
    removed: list[dict[str, str]] = field(default_factory=list)
    citations: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.removed


def _strip_quotes(text: str) -> str:
    """Remove verbatim quoted spans ("...") before linting for speculation."""
    return re.sub(r"\"[^\"]*\"|“[^”]*”", "", text)


def validate_markdown(
    markdown: str,
    known_ids: set[str],
    speculation_patterns: tuple[str, ...] = DEFAULT_SPECULATION_PATTERNS,
    require_citations: bool = True,
) -> ValidationResult:
    lint = [re.compile(p, re.IGNORECASE) for p in speculation_patterns]
    kept_lines: list[str] = []
    removed: list[dict[str, str]] = []
    cited: set[str] = set()

    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", ">")) or stripped in {"---", "***"}:
            kept_lines.append(line)
            continue
        prefix = ""
        body = stripped
        m = re.match(r"^((?:[-*+]|\d+\.)\s+)", stripped)
        if m:
            prefix, body = m.group(1), stripped[m.end() :]

        sentences = _SENTENCE_SPLIT.split(body) if body else []
        kept: list[str] = []
        for sentence in sentences:
            hit = next((p.pattern for p in lint if p.search(_strip_quotes(sentence))), None)
            if hit:
                removed.append({"text": sentence, "reason": f"speculative language: {hit}"})
                continue
            ids = {f"{k}{v}" for k, v in CITATION_RE.findall(sentence)}
            unknown = {i for i in ids if i not in known_ids}
            if require_citations and not ids:
                removed.append({"text": sentence, "reason": "no citation"})
                continue
            if unknown:
                removed.append(
                    {
                        "text": sentence,
                        "reason": f"unknown citation(s): {', '.join(sorted(unknown))}",
                    }
                )
                continue
            kept.append(sentence)
            cited |= ids
        if kept:
            indent = line[: len(line) - len(line.lstrip())]
            kept_lines.append(indent + prefix + " ".join(kept))

    # collapse runs of blank lines left behind
    out: list[str] = []
    for line in kept_lines:
        if line.strip() == "" and out and out[-1].strip() == "":
            continue
        out.append(line)
    return ValidationResult("\n".join(out).strip() + "\n", removed, cited)
