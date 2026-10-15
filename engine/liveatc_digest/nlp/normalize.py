"""Phraseology normalization: spoken numbers, letters, and ATC shorthand to a canonical form.

``normalize_text`` keeps word order and spacing stable so character spans found in the
normalized text can be mapped back to the original words by token index.
"""

from __future__ import annotations

import re

DIGIT_WORDS: dict[str, str] = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "tree": "3",
    "four": "4",
    "fower": "4",
    "five": "5",
    "fife": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "niner": "9",
}

PHONETIC: dict[str, str] = {
    "alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E",
    "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I", "juliet": "J", "juliett": "J",
    "kilo": "K", "lima": "L", "mike": "M", "november": "N", "oscar": "O", "papa": "P",
    "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
    "whiskey": "W", "xray": "X", "x-ray": "X", "yankee": "Y", "zulu": "Z",
}  # fmt: skip

_SCALE = {"hundred": 100, "thousand": 1000}
_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*|\d+(?:\.\d+)?|[^\sA-Za-z\d]")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def spoken_number(words: list[str]) -> int | None:
    """Parse a spoken quantity such as ``two thousand five hundred`` or ``niner thousand``.

    Digit-by-digit strings (``one two three``) are handled by ``normalize_text``; this handles
    the hundreds/thousands grammar used for altitudes and headings.
    """
    total = 0
    current = 0
    seen = False
    for w in words:
        w = w.lower()
        if w in DIGIT_WORDS:
            current = (
                current * 10 + int(DIGIT_WORDS[w]) if seen and current < 10 else int(DIGIT_WORDS[w])
            )
            seen = True
        elif w in _TEENS:
            current = _TEENS[w]
            seen = True
        elif w in _TENS:
            current = _TENS[w]
            seen = True
        elif w in _SCALE:
            if not seen:
                current = 1
            current *= _SCALE[w]
            if w == "thousand":
                total += current
                current = 0
            seen = True
        elif w in {"and"}:
            continue
        else:
            return None
    if not seen:
        return None
    return total + current


def normalize_text(text: str) -> str:
    """Lowercase, collapse spoken digits into digit strings, and expand ATC shorthand.

    Examples::

        "cleared to land runway two niner"  -> "cleared to land runway 29"
        "november one two three alpha bravo" -> "n123ab"
        "climb and maintain five thousand"   -> "climb and maintain 5000"
        "flight level three five zero"       -> "fl350"
    """
    tokens = tokenize(text)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i].lower()

        # "flight level <digits>" -> fl###
        if tok == "flight" and i + 1 < len(tokens) and tokens[i + 1].lower() == "level":
            j = i + 2
            digits = []
            while j < len(tokens) and tokens[j].lower() in DIGIT_WORDS:
                digits.append(DIGIT_WORDS[tokens[j].lower()])
                j += 1
            if digits:
                out.append("fl" + "".join(digits))
                i = j
                continue

        # hundreds / thousands grammar
        if tok in DIGIT_WORDS or tok in _TEENS or tok in _TENS:
            j = i
            span: list[str] = []
            while j < len(tokens) and (
                tokens[j].lower() in DIGIT_WORDS
                or tokens[j].lower() in _TEENS
                or tokens[j].lower() in _TENS
                or tokens[j].lower() in _SCALE
                or (
                    tokens[j].lower() == "and"
                    and span
                    and j + 1 < len(tokens)
                    and tokens[j + 1].lower() in DIGIT_WORDS
                )
            ):
                span.append(tokens[j].lower())
                j += 1
            if any(w in _SCALE for w in span):
                value = spoken_number(span)
                if value is not None:
                    out.append(str(value))
                    i = j
                    continue
            # digit-by-digit run, possibly mixed with phonetic letters (callsigns)
            j = i
            run: list[str] = []
            while j < len(tokens) and (
                tokens[j].lower() in DIGIT_WORDS or tokens[j].lower() in PHONETIC
            ):
                t = tokens[j].lower()
                run.append(DIGIT_WORDS.get(t) or PHONETIC[t].lower())
                j += 1
            if run:
                out.append("".join(run))
                i = j
                continue

        if tok in PHONETIC:
            j = i
            run = []
            while j < len(tokens) and (
                tokens[j].lower() in PHONETIC or tokens[j].lower() in DIGIT_WORDS
            ):
                t = tokens[j].lower()
                run.append(DIGIT_WORDS.get(t) or PHONETIC[t].lower())
                j += 1
            # a lone phonetic word is usually a taxiway or a word like "delta"; keep it as-is
            if len(run) >= 2:
                out.append("".join(run))
                i = j
                continue

        if tok == "point" and out and out[-1].isdigit() and i + 1 < len(tokens):
            # "one two one point seven" -> 121.7
            j = i + 1
            digits = []
            while j < len(tokens) and tokens[j].lower() in DIGIT_WORDS:
                digits.append(DIGIT_WORDS[tokens[j].lower()])
                j += 1
            if digits:
                out[-1] = out[-1] + "." + "".join(digits)
                i = j
                continue

        out.append(tok)
        i += 1

    text_out = " ".join(out)
    text_out = re.sub(r"\s+([,.;:?!])", r"\1", text_out)
    return text_out
