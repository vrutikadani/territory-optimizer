"""Lead routing rules, written as plain code so sales leaders can read them.

Mirrors the CRM workflow logic:
  1. A lead a rep manually handed to another rep is never overwritten.
  2. Non-medical specialties -> inside sales.
  3. Med spas with no medical director -> inside sales (they cannot buy the device yet).
  4. Medical specialties (or blank), and any SQL / opportunity -> the outside rep who covers that ZIP.
  5. No outside coverage (e.g. a region with no field rep) -> inside sales.
  6. The company owner follows its contacts (contact-driven), it is not routed on its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

MEDICAL = {"plastic surgery", "dermatology", "med spa"}


@dataclass
class Contact:
    contact_id: str
    company_id: str
    zip: str
    specialty: str = ""
    stage: str = "MQL"                 # MQL | SQL | Opportunity
    has_medical_director: Optional[bool] = None
    owner: Optional[str] = None
    manual_owner_lock: bool = False    # set when a rep hands a lead over by hand


@dataclass
class Decision:
    owner: str
    rule: str


class Router:
    def __init__(self, zip_to_outside_rep: dict[str, str], inside_reps: list[str]):
        self.zip_map, self.inside = zip_to_outside_rep, inside_reps
        self._i = 0

    def _next_inside(self) -> str:
        rep = self.inside[self._i % len(self.inside)]
        self._i += 1
        return rep

    def route(self, c: Contact) -> Decision:
        if c.manual_owner_lock and c.owner:
            return Decision(c.owner, "1 manual handoff kept")
        spec = c.specialty.strip().lower()
        if c.stage == "MQL" and spec and spec not in MEDICAL:
            return Decision(self._next_inside(), "2 non-medical MQL -> inside")
        if c.stage == "MQL" and spec == "med spa" and not c.has_medical_director:
            return Decision(self._next_inside(), "3 med spa without medical director -> inside")
        outside = self.zip_map.get(c.zip)
        if outside:
            return Decision(outside, "4 medical / SQL -> covering outside rep")
        return Decision(self._next_inside(), "5 no outside coverage -> inside")

    def route_all(self, contacts: list[Contact]) -> dict[str, Decision]:
        """Route contacts, then set each company owner from its first routed contact."""
        out: dict[str, Decision] = {}
        company_owner: dict[str, str] = {}
        for c in contacts:
            d = self.route(c)
            c.owner = d.owner
            out[c.contact_id] = d
            company_owner.setdefault(c.company_id, d.owner)
        for cid, owner in company_owner.items():
            out[f"company:{cid}"] = Decision(owner, "6 company follows its contacts")
        return out
