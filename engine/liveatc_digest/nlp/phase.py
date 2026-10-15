"""Classify an utterance as ground / terminal / enroute from feed role and content."""

from __future__ import annotations

from enum import StrEnum

from .events import AtcEvent


class Phase(StrEnum):
    GROUND = "ground"
    TERMINAL = "terminal"
    ENROUTE = "enroute"


_GROUND_EVENTS = {"taxi", "hold_short", "cross_runway", "line_up_and_wait"}
_AIR_EVENTS = {
    "takeoff_clearance", "landing_clearance", "option_clearance", "go_around",
    "approach_clearance", "heading", "altitude", "speed", "traffic_advisory",
}  # fmt: skip


def classify_phase(feed_role: str, events: list[AtcEvent]) -> Phase:
    types = {e.type for e in events}
    if feed_role == "center":
        return Phase.ENROUTE
    if feed_role in {"clearance", "ground", "atis"}:
        return Phase.GROUND
    if feed_role in {"approach", "departure"}:
        return Phase.TERMINAL
    # tower / ctaf / unknown: decide from content, defaulting to the terminal environment
    if types & _GROUND_EVENTS and not types & _AIR_EVENTS:
        return Phase.GROUND
    return Phase.TERMINAL
