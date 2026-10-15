from liveatc_digest.nlp import classify_phase, extract_callsigns, extract_events, normalize_text
from liveatc_digest.nlp.normalize import spoken_number


def test_normalize_digits_and_phonetics():
    assert normalize_text("cleared to land runway two niner") == "cleared to land runway 29"
    assert normalize_text("November one two three alpha bravo") == "n123ab"
    assert normalize_text("climb and maintain five thousand") == "climb and maintain 5000"
    assert (
        normalize_text("descend and maintain two thousand five hundred")
        == "descend and maintain 2500"
    )
    assert normalize_text("flight level three five zero") == "fl350"
    assert normalize_text("contact tower one one eight point five") == "contact tower 118.5"
    assert normalize_text("taxi via delta") == "taxi via delta"  # lone phonetic word kept


def test_spoken_number():
    assert spoken_number(["five", "thousand"]) == 5000
    assert spoken_number(["two", "thousand", "five", "hundred"]) == 2500
    assert spoken_number(["eleven", "thousand"]) == 11000
    assert spoken_number(["hundred"]) == 100
    assert spoken_number(["runway"]) is None


def test_callsigns():
    cs = extract_callsigns("Cessna one two three alpha bravo, runway two nine cleared to land")
    assert [c.callsign for c in cs] == ["N123AB"]
    assert cs[0].kind == "n_number"
    cs = extract_callsigns("JetBlue twelve thirty four contact departure")
    assert cs == [] or all(c.kind == "partial" for c in cs)
    cs = extract_callsigns("jetblue one two three four contact departure")
    assert [c.callsign for c in cs] == ["JBU1234"]
    cs = extract_callsigns("three alpha bravo turn left heading two seven zero")
    assert cs[0].callsign == "3AB" and cs[0].kind == "partial"


def test_events_and_spans():
    text = (
        "Cessna one two three alpha bravo runway two nine cleared for takeoff, "
        "contact departure one two four point four"
    )
    ev = extract_events(text)
    types = [e.type for e in ev]
    assert "takeoff_clearance" in types and "frequency_change" in types
    tko = next(e for e in ev if e.type == "takeoff_clearance")
    assert tko.fields["runway"] == "29"
    norm = normalize_text(text)
    assert norm[tko.span[0] : tko.span[1]] == tko.text
    fc = next(e for e in ev if e.type == "frequency_change")
    assert fc.fields == {"facility": "departure", "mhz": "124.4"}


def test_more_events():
    ev = extract_events("Hold short runway one one, traffic is a Cessna two o'clock")
    assert [e.type for e in ev] == ["hold_short", "traffic_advisory"]
    ev = extract_events(
        "Cleared ILS runway two niner approach, maintain three thousand until established"
    )
    assert ev[0].type == "approach_clearance" and ev[0].fields == {
        "approach": "ILS",
        "runway": "29",
    }
    assert any(e.type == "altitude" and e.fields["altitude"] == "3000" for e in ev)
    ev = extract_events("Mayday mayday mayday, engine failure")
    assert ev[0].type == "emergency"
    ev = extract_events("Squawk four five one two, altimeter three zero one two")
    assert {e.type for e in ev} == {"squawk", "altimeter"}


def test_phase_classification():
    assert classify_phase("center", []) == "enroute"
    assert classify_phase("ground", []) == "ground"
    assert classify_phase("approach", []) == "terminal"
    taxi = extract_events("taxi via alpha hold short runway two nine")
    assert classify_phase("tower", taxi) == "ground"
    land = extract_events("runway two nine cleared to land")
    assert classify_phase("tower", land) == "terminal"
    assert classify_phase("tower", []) == "terminal"
