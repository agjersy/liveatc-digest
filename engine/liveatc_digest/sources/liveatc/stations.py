"""Station discovery by scraping the LiveATC.net airport search page.

Adapted from agjersy/liveatc-downloader (the former ``liveatc_downloader`` submodule).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from bs4 import BeautifulSoup

from ...http import HttpClient

SEARCH_URL = "https://www.liveatc.net/search/?icao={icao}"
_MOUNT_RE = re.compile(r"/archive\.php\?m=([A-Za-z0-9_]+)")


class Role(StrEnum):
    CLEARANCE = "clearance"
    GROUND = "ground"
    TOWER = "tower"
    APPROACH = "approach"
    DEPARTURE = "departure"
    CENTER = "center"
    CTAF = "ctaf"
    ATIS = "atis"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Frequency:
    title: str
    mhz: str

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "mhz": self.mhz}


@dataclass(frozen=True)
class Station:
    icao: str
    identifier: str  # LiveATC mount name, e.g. kbed_twr
    title: str
    up: bool
    frequencies: tuple[Frequency, ...] = field(default_factory=tuple)

    @property
    def role(self) -> Role:
        return infer_role(self.identifier, self.title)


_ROLE_HINTS: list[tuple[Role, tuple[str, ...]]] = [
    (Role.CLEARANCE, ("_del", "clnc", "clearance", "delivery")),
    (Role.GROUND, ("_gnd", "ground", "ramp")),
    (Role.ATIS, ("atis",)),
    (Role.TOWER, ("_twr", "tower")),
    (Role.APPROACH, ("_app", "approach", "arrival", "tracon", "final")),
    (Role.DEPARTURE, ("_dep", "departure")),
    (Role.CENTER, ("_ctr", "center", "centre", "artcc")),
    (Role.CTAF, ("ctaf", "unicom", "multicom")),
]


def infer_role(identifier: str, title: str = "") -> Role:
    haystack = f"{identifier} {title}".lower()
    for role, hints in _ROLE_HINTS:
        if any(h in haystack for h in hints):
            return role
    return Role.UNKNOWN


def parse_stations(html: str, icao: str) -> list[Station]:
    """Parse the search page into stations.

    The page lists each feed in a ``table.body`` block (title in ``<strong>``, status in a
    ``<font>`` reading ``UP``/``DOWN``, and an ``/archive.php?m=<mount>`` link), followed by a
    ``table.freqTable`` with one frequency per row.
    """
    soup = BeautifulSoup(html, "html.parser")
    stations: list[Station] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=_MOUNT_RE):
        mount = _MOUNT_RE.search(link["href"]).group(1)
        if mount in seen:
            continue
        table = link.find_parent("table")
        if table is None:
            continue
        strong = table.find("strong")
        title = strong.get_text(strip=True) if strong else mount
        font = table.find("font")
        up = (font.get_text(strip=True).upper() == "UP") if font else True

        freqs: list[Frequency] = []
        freq_table = table.find_next("table", class_="freqTable")
        if freq_table is not None:
            for row in freq_table.find_all("tr"):
                cells = [c.get_text(strip=True) for c in row.find_all("td")]
                if len(cells) >= 2 and re.match(r"^\d{2,3}\.\d{1,3}$", cells[1]):
                    freqs.append(Frequency(cells[0], cells[1]))

        seen.add(mount)
        stations.append(Station(icao.upper(), mount, title, up, tuple(freqs)))
    return stations


def fetch_stations(client: HttpClient, icao: str) -> list[Station]:
    html = client.get_text(SEARCH_URL.format(icao=icao.lower()))
    return parse_stations(html, icao)
