"""Geocoding that survives bad data, rate limits and restarts.

Why this exists: an earlier geocoding job (a Node.js script) failed halfway through a
large account list. It had no cache, no retry and no checkpoint, so a restart meant
paying for and waiting on every address again, and partial results were written over
good ones. This version fixes each of those:

  * cache    - an address is geocoded once, ever (JSON file keyed by normalized address)
  * retry    - exponential backoff on rate limits / transient errors
  * checkpoint - the cache is flushed every N rows, so a crash loses at most N rows
  * no overwrite - a failed lookup never replaces a good stored coordinate
  * fallback - if the street address fails, fall back to the ZIP centroid and say so
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol


@dataclass
class GeoResult:
    lat: float
    lon: float
    precision: str  # "rooftop" | "zip_centroid"


class Provider(Protocol):
    def lookup(self, address: str) -> Optional[GeoResult]: ...


class RateLimited(Exception):
    pass


def normalize(address: str) -> str:
    a = address.upper().strip()
    a = re.sub(r"[.,#]", " ", a)
    a = re.sub(r"\bSTREET\b", "ST", a)
    a = re.sub(r"\bAVENUE\b", "AVE", a)
    a = re.sub(r"\bSUITE\b|\bSTE\b", "STE", a)
    return re.sub(r"\s+", " ", a)


class ZipCentroidProvider:
    """Offline provider backed by a ZIP -> lat/lon table."""

    def __init__(self, table: dict[str, tuple[float, float]]):
        self.table = table

    def lookup(self, address: str) -> Optional[GeoResult]:
        m = re.search(r"\b(\d{5})\b", address)
        if m and m.group(1) in self.table:
            lat, lon = self.table[m.group(1)]
            return GeoResult(lat, lon, "zip_centroid")
        return None


class GoogleProvider:  # pragma: no cover - needs a key and network
    URL = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(self, key: str | None = None):
        import requests
        self._s, self.key = requests.Session(), key or os.environ["GOOGLE_MAPS_KEY"]

    def lookup(self, address: str) -> Optional[GeoResult]:
        r = self._s.get(self.URL, params={"address": address, "key": self.key}, timeout=10).json()
        if r.get("status") == "OVER_QUERY_LIMIT":
            raise RateLimited()
        if r.get("status") != "OK":
            return None
        loc = r["results"][0]["geometry"]["location"]
        return GeoResult(loc["lat"], loc["lng"], "rooftop")


class CachedGeocoder:
    def __init__(self, provider: Provider, cache_path: str | Path, fallback: Provider | None = None,
                 checkpoint_every: int = 50, max_retries: int = 4, sleep: Callable[[float], None] = time.sleep):
        self.provider, self.fallback = provider, fallback
        self.cache_path = Path(cache_path)
        self.cache: dict[str, dict] = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self.checkpoint_every, self.max_retries, self.sleep = checkpoint_every, max_retries, sleep
        self.stats = {"cache_hit": 0, "api_call": 0, "fallback": 0, "failed": 0, "retries": 0}
        self._dirty = 0

    def _call(self, provider: Provider, address: str) -> Optional[GeoResult]:
        for attempt in range(self.max_retries + 1):
            try:
                return provider.lookup(address)
            except RateLimited:
                if attempt == self.max_retries:
                    raise
                self.stats["retries"] += 1
                self.sleep(min(2 ** attempt, 30))
        return None

    def geocode(self, address: str) -> Optional[GeoResult]:
        key = normalize(address)
        if key in self.cache:
            self.stats["cache_hit"] += 1
            return GeoResult(**self.cache[key])

        self.stats["api_call"] += 1
        res = self._call(self.provider, address)
        if res is None and self.fallback is not None:
            res = self._call(self.fallback, address)
            if res:
                self.stats["fallback"] += 1
        if res is None:
            self.stats["failed"] += 1
            return None  # never overwrite: nothing is stored for a failure

        self.cache[key] = res.__dict__
        self._dirty += 1
        if self._dirty >= self.checkpoint_every:
            self.flush()
        return res

    def flush(self) -> None:
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache, indent=0))
        tmp.replace(self.cache_path)  # atomic: a crash mid-write cannot corrupt the cache
        self._dirty = 0


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))
