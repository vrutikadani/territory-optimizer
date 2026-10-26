"""Territory toolkit demo on synthetic accounts: geocoding, naming, balance. No keys needed.

    python run_demo.py
"""
import random
import tempfile
from pathlib import Path

from territory import naming
from territory.balance import Account, books, spread, suggest_moves
from territory.geocode import CachedGeocoder, ZipCentroidProvider

random.seed(11)

# 1) Geocoding with cache + fallback -------------------------------------------
zips = {"78701": (30.27, -97.74), "77002": (29.76, -95.37), "75201": (32.79, -96.80),
        "33131": (25.77, -80.19), "30303": (33.75, -84.39), "10001": (40.75, -73.99)}
cache = Path(tempfile.gettempdir()) / "geo_cache_demo.json"
cache.unlink(missing_ok=True)
geo = CachedGeocoder(ZipCentroidProvider(zips), cache)
addresses = [f"{random.randint(1, 999)} Main Street, City {z}" for z in random.choices(list(zips), k=40)]
for a in addresses + addresses[:10]:  # second pass hits the cache
    geo.geocode(a)
geo.flush()
print("1) Geocoding:", geo.stats)

# 2) Naming convention ----------------------------------------------------------
print("\n2) Naming")
for states, cities in [(["TX", "LA"], ["Dallas", "IAH"]), (["MS", "AL"], ["MEM", "Atlanta"])]:
    tid, name = naming.build(states, cities)
    print(f"   ID: {tid:<32} Name: {name}")
for old in ["Temp JORDAN - South", "Riley/Chen LAX"]:
    print(f"   lint '{old}':", naming.lint(old, {"Jordan Blake", "Riley Chen"}))

# 3) Balance ---------------------------------------------------------------------
terr = {"Pacific": {"CA", "NV", "OR", "WA"}, "Mountain": {"UT", "CO", "AZ", "NM", "ID"},
        "South Central": {"TX", "OK", "LA", "AR"}, "Southeast": {"FL", "GA", "AL", "MS", "SC", "NC", "TN"}}
weights = {"CA": 9, "TX": 7, "FL": 6, "AZ": 3, "GA": 3, "NC": 3, "CO": 2, "WA": 2, "TN": 2}
accts = []
for i in range(600):
    st = random.choices(list(weights) + ["NV", "OR", "UT", "NM", "ID", "OK", "LA", "AR", "AL", "MS", "SC"],
                        list(weights.values()) + [1] * 11)[0]
    accts.append(Account(f"A{i}", st, random.uniform(5_000, 60_000)))
b = books(terr, accts)
print(f"\n3) Balance - spread before: {spread(b):.2f}x")
for t, v in b.items():
    print(f"   {t:<14} {v['accounts']:>4} accts  ${v['revenue']/1e6:.1f}M")
for m in suggest_moves(terr, accts):
    print(f"   move {m['state']} {m['from']} -> {m['to']}: spread {m['spread_before']}x -> {m['spread_after']}x")
