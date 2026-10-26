"""Territory balance: are reps carrying fair books? If not, which state moves fix it?

A territory is a set of states. Each account has a state and trailing revenue.
We score balance as the spread of revenue (max/min) and suggest single-state moves
between neighboring territories that shrink the spread the most.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

# a small adjacency map is enough to keep moves geographic (no "Florida to Oregon")
NEIGHBORS = {
    "WA": {"OR", "ID"}, "OR": {"WA", "CA", "ID", "NV"}, "CA": {"OR", "NV", "AZ"}, "NV": {"CA", "OR", "ID", "UT", "AZ"},
    "AZ": {"CA", "NV", "UT", "NM"}, "UT": {"NV", "ID", "WY", "CO", "AZ"}, "ID": {"WA", "OR", "NV", "UT", "WY", "MT"},
    "CO": {"UT", "WY", "NE", "KS", "OK", "NM"}, "NM": {"AZ", "CO", "OK", "TX"}, "TX": {"NM", "OK", "AR", "LA"},
    "OK": {"TX", "NM", "CO", "KS", "MO", "AR"}, "KS": {"CO", "NE", "MO", "OK"}, "MO": {"KS", "NE", "IA", "IL", "KY", "TN", "AR", "OK"},
    "LA": {"TX", "AR", "MS"}, "AR": {"TX", "OK", "MO", "TN", "MS", "LA"}, "MS": {"LA", "AR", "TN", "AL"},
    "AL": {"MS", "TN", "GA", "FL"}, "GA": {"AL", "TN", "NC", "SC", "FL"}, "FL": {"AL", "GA"},
    "TN": {"KY", "VA", "NC", "GA", "AL", "MS", "AR", "MO"}, "NC": {"VA", "TN", "GA", "SC"}, "SC": {"NC", "GA"},
    "IL": {"WI", "IA", "MO", "KY", "IN"}, "IN": {"IL", "MI", "OH", "KY"}, "OH": {"MI", "IN", "KY", "WV", "PA"},
    "KY": {"IL", "IN", "OH", "WV", "VA", "TN", "MO"}, "PA": {"NY", "NJ", "DE", "MD", "WV", "OH"},
    "NY": {"PA", "NJ", "CT", "MA", "VT"}, "NJ": {"NY", "PA", "DE"},
}


@dataclass
class Account:
    account_id: str
    state: str
    revenue: float


def books(territories: dict[str, set[str]], accounts: list[Account]) -> dict[str, dict]:
    state_owner = {s: t for t, states in territories.items() for s in states}
    out = defaultdict(lambda: {"accounts": 0, "revenue": 0.0})
    for t in territories:
        out[t]
    for a in accounts:
        t = state_owner.get(a.state)
        if t:
            out[t]["accounts"] += 1
            out[t]["revenue"] += a.revenue
    return dict(out)


def spread(b: dict[str, dict]) -> float:
    revs = [v["revenue"] for v in b.values()]
    return max(revs) / max(min(revs), 1.0)


def suggest_moves(territories: dict[str, set[str]], accounts: list[Account], max_moves: int = 3) -> list[dict]:
    terr = {t: set(s) for t, s in territories.items()}
    moves = []
    for _ in range(max_moves):
        current = spread(books(terr, accounts))
        best = None
        for src, states in terr.items():
            if len(states) <= 1:
                continue
            for s in states:
                for dst, dstates in terr.items():
                    if dst == src or not (NEIGHBORS.get(s, set()) & dstates):
                        continue
                    trial = {t: set(v) for t, v in terr.items()}
                    trial[src].discard(s)
                    trial[dst].add(s)
                    new = spread(books(trial, accounts))
                    if new < current and (best is None or new < best[0]):
                        best = (new, s, src, dst)
        if not best:
            break
        new, s, src, dst = best
        terr[src].discard(s)
        terr[dst].add(s)
        moves.append({"state": s, "from": src, "to": dst, "spread_before": round(current, 2), "spread_after": round(new, 2)})
    return moves
