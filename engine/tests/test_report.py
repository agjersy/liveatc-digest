from datetime import UTC, datetime

from liveatc_digest.llm import LlmResponse
from liveatc_digest.report import DigestInput, UtteranceView, build_digest, validate_markdown


def test_validator_removes_uncited_and_speculative_sentences():
    md = """## Terminal

N123AB was cleared to land on runway 29 [u1]. The pilot should have gone around.
This sentence has no citation.
Another one cites something unknown [u99].

- Bullet with a citation [u2]. Bullet sentence without.
- "The pilot failed to" is inside a quote so it is fine [u1].
"""
    res = validate_markdown(md, {"u1", "u2"})
    assert not res.ok
    reasons = [r["reason"] for r in res.removed]
    assert any("no citation" in r for r in reasons)
    assert any("speculative" in r for r in reasons)
    assert any("unknown citation" in r for r in reasons)
    assert "should have" not in res.markdown
    assert "N123AB was cleared to land on runway 29 [u1]." in res.markdown
    assert "- Bullet with a citation [u2]." in res.markdown
    assert 'failed to" is inside a quote' in res.markdown
    assert res.citations == {"u1", "u2"}


def _views():
    t0 = datetime(2024, 7, 28, 0, 1, tzinfo=UTC)
    return [
        UtteranceView(
            1,
            t0,
            t0.replace(second=5),
            "kbed_twr",
            "tower",
            "terminal",
            "Cessna one two three alpha bravo runway two nine cleared to land",
            "cessna 123ab runway 29 cleared to land",
            0.8,
            ["N123AB"],
            [
                {
                    "id": 7,
                    "type": "landing_clearance",
                    "fields": {"runway": "29"},
                    "text": "runway 29 cleared to land",
                }
            ],
        ),
        UtteranceView(
            2,
            t0.replace(minute=3),
            t0.replace(minute=3, second=4),
            "kbed_gnd",
            "ground",
            "ground",
            "taxi via alpha hold short runway two nine",
            "taxi via alpha hold short runway 29",
            0.3,
            [],
            [
                {
                    "id": 8,
                    "type": "hold_short",
                    "fields": {"runway": "29"},
                    "text": "hold short runway 29",
                }
            ],
        ),
    ]


def test_skeleton_digest_is_fully_cited():
    inp = DigestInput(
        "KBED",
        datetime(2024, 7, 28, 0, 0, tzinfo=UTC),
        datetime(2024, 7, 28, 0, 30, tzinfo=UTC),
        _views(),
    )
    res = build_digest(inp, llm=None)
    assert res.narrative_markdown is None
    assert "## Terminal activity" in res.markdown and "## Ground activity" in res.markdown
    assert "**N123AB**" in res.markdown and "[e7]" in res.markdown
    assert "(low confidence)" in res.markdown
    check = validate_markdown(res.markdown, {"u1", "u2", "e7", "e8"})
    assert check.ok, check.removed


class FakeLlm:
    name = "fake"
    model = "fake-1"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = []

    def complete(self, system, user, json_mode=False, max_tokens=2048):
        self.calls.append((system, user))
        return LlmResponse(self.text, self.name, self.model)


def test_llm_narrative_is_validated():
    llm = FakeLlm(
        "## Terminal\n\nN123AB received a landing clearance for runway 29 [u1]. "
        "The pilot probably caused confusion. Something uncited.\n"
    )
    inp = DigestInput(
        "KBED",
        datetime(2024, 7, 28, 0, 0, tzinfo=UTC),
        datetime(2024, 7, 28, 0, 30, tzinfo=UTC),
        _views(),
    )
    res = build_digest(inp, llm=llm)
    assert res.llm_provider == "fake"
    assert "# Narrative" in res.markdown
    assert "landing clearance for runway 29 [u1]" in res.markdown
    assert "probably caused" not in res.markdown and "Something uncited" not in res.markdown
    assert len(res.validation.removed) == 2
    assert "[u1]" in llm.calls[0][1] and "Never infer" in llm.calls[0][0]
