"""Territory naming convention.

Old labels had rep names baked in ("Temp JORDAN", "Riley/Chen"), so every
staffing change broke reports. New rule: a territory has an ID and a Name that
describe *geography only*.

  * ID   - states as postal codes, cities spelled out:  "MS, AL, Memphis, Atlanta"
  * Name - states spelled out, cities spelled out:       "Mississippi, Alabama, Memphis, Atlanta"
  * No airport codes, no rep names, no "temp".
  * Anchor on the big state; if the territory is many small states, anchor on the big city.
"""
from __future__ import annotations

import re

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "PR": "Puerto Rico", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
AIRPORT_CODES = {"LAX": "Los Angeles", "SFO": "San Francisco", "ATL": "Atlanta", "DFW": "Dallas",
                 "ORD": "Chicago", "JFK": "New York City", "MEM": "Memphis", "IAH": "Houston"}
BANNED_WORDS = {"temp", "tbd", "open"}


def build(states: list[str], cities: list[str] | None = None) -> tuple[str, str]:
    cities = [AIRPORT_CODES.get(c.upper(), c) for c in (cities or [])]
    unknown = [s for s in states if s.upper() not in STATES]
    if unknown:
        raise ValueError(f"unknown state codes: {unknown}")
    tid = ", ".join([s.upper() for s in states] + cities)
    name = ", ".join([STATES[s.upper()] for s in states] + cities)
    return tid, name


def lint(label: str, rep_names: set[str]) -> list[str]:
    """Flag labels that break the convention."""
    problems = []
    words = {w for w in re.split(r"[^a-z]+", label.lower()) if w}
    if words & BANNED_WORDS:
        problems.append(f"contains a staffing word: {sorted(words & BANNED_WORDS)}")
    for rep in rep_names:
        if any(part.lower() in words for part in rep.split()):
            problems.append(f"contains rep name '{rep}'")
    for code in AIRPORT_CODES:
        if code.lower() in words:
            problems.append(f"uses airport code {code}; spell out {AIRPORT_CODES[code]}")
    return problems
