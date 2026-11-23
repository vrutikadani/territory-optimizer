"""Geocoding that survives bad data, rate limits and restarts.

Why this exists: an earlier geocoding job (a Node.js script) failed halfway through a
large account list. It had no cache, no retry and no checkpoint, so a restart meant
paying for and waiting on every address again, and partial results were written over
good ones. This version fixes each of those:

  * cache    - an address is geocoded once, ever (JSON file keyed by normalized address)
  * retry    - exponential backoff on rate limits, timeouts and dropped connections
  * checkpoint - the cache is flushed every N rows, and again on exit or error
  * no overwrite - a failed lookup never replaces a good stored coordinate
  * fallback - if the street address fails, fall back to the ZIP centroid and say so;
                a fallback after a transient error is flagged so it is retried later
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


try:  # requests is optional; it is only needed for live Google lookups
    import requests as _requests
    _NETWORK_ERRORS: tuple[type[BaseException], ...] = (_requests.RequestException,)
except ImportError:  # pragma: no cover
    _NETWORK_ERRORS = ()


@dataclass
class GeoResult:
    lat: float
    lon: float
    # "rooftop" | "range_interpolated" | "geometric_center" | "approximate" | "unknown" | "zip_centroid"
    precision: str


class Provider(Protocol):
    def lookup(self, address: str) -> Optional[GeoResult]:
        """Return a result, None when the address is not found, or raise TransientError."""


class TransientError(Exception):
    """A failure worth retrying: timeout, dropped connection, server-side error."""


class RateLimited(TransientError):
    pass


class ProviderError(Exception):
    """A failure that retrying will not fix, such as a bad API key. Stops the run."""


# Errors that are retried with backoff. Anything else propagates (after a flush).
TRANSIENT = (TransientError, TimeoutError, ConnectionError) + _NETWORK_ERRORS


def normalize(address: str) -> str:
    a = address.upper().strip()
    a = re.sub(r"[.,#]", " ", a)
    a = re.sub(r"\bSTREET\b", "ST", a)
    a = re.sub(r"\bAVENUE\b", "AVE", a)
    a = re.sub(r"\bSUITE\b|\bSTE\b", "STE", a)
    return re.sub(r"\s+", " ", a)


_TRAILING_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?(?:\s*,?\s*(?:USA|US|UNITED STATES))?\s*$", re.I)
_ANY_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def extract_zip(address: str) -> Optional[str]:
    """The ZIP at the end of the address. A leading 5-digit house number is never a ZIP."""
    text = address.strip()
    m = _TRAILING_ZIP.search(text)
    if m and (m.start() > 0 or m.end() == len(text)):
        # a trailing ZIP, or the whole input is a ZIP ("78701", "78701, USA")
        return m.group(1)
    later = [m for m in _ANY_ZIP.finditer(text) if m.start() > 0]
    return later[-1].group(1) if later else None


class ZipCentroidProvider:
    """Offline provider backed by a ZIP -> lat/lon table."""

    def __init__(self, table: dict[str, tuple[float, float]]):
        self.table = table

    def lookup(self, address: str) -> Optional[GeoResult]:
        z = extract_zip(address)
        if z and z in self.table:
            lat, lon = self.table[z]
            return GeoResult(lat, lon, "zip_centroid")
        return None


class GoogleProvider:
    URL = "https://maps.googleapis.com/maps/api/geocode/json"
    LOCATION_TYPES = {"ROOFTOP": "rooftop", "RANGE_INTERPOLATED": "range_interpolated",
                      "GEOMETRIC_CENTER": "geometric_center", "APPROXIMATE": "approximate"}

    def __init__(self, key: str | None = None, session=None, timeout: float = 10):
        if session is None:  # pragma: no cover - needs network
            import requests
            session = requests.Session()
        self._s, self.timeout = session, timeout
        self.key = key or os.environ["GOOGLE_MAPS_KEY"]

    def lookup(self, address: str) -> Optional[GeoResult]:
        try:
            r = self._s.get(self.URL, params={"address": address, "key": self.key}, timeout=self.timeout).json()
        except TRANSIENT as e:
            raise TransientError(f"network error: {e}") from e
        except ValueError as e:  # body was not JSON, e.g. a proxy error page
            raise TransientError(f"bad response: {e}") from e
        status = r.get("status")
        if status == "OVER_QUERY_LIMIT":
            raise RateLimited()
        if status == "UNKNOWN_ERROR":
            raise TransientError("UNKNOWN_ERROR")
        if status in ("ZERO_RESULTS", "INVALID_REQUEST"):
            return None
        if status != "OK":
            raise ProviderError(f"{status}: {r.get('error_message', '')}".strip())
        geom = r["results"][0]["geometry"]
        loc = geom["location"]
        return GeoResult(loc["lat"], loc["lng"], self.LOCATION_TYPES.get(geom.get("location_type"), "unknown"))


class CachedGeocoder:
    """Cache entries are {lat, lon, precision, retry} or {status: "not_found", retry: false}.

    retry=true marks a ZIP fallback stored after a transient failure. It is used, but the
    primary provider is tried again the next time the address comes up.
    """

    def __init__(self, provider: Provider, cache_path: str | Path, fallback: Provider | None = None,
                 checkpoint_every: int = 50, max_retries: int = 4, sleep: Callable[[float], None] = time.sleep):
        self.provider, self.fallback = provider, fallback
        self.cache_path = Path(cache_path)
        self.cache: dict[str, dict] = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self.checkpoint_every, self.max_retries, self.sleep = checkpoint_every, max_retries, sleep
        self.stats = {"cache_hit": 0, "api_call": 0, "fallback": 0, "failed": 0, "retries": 0,
                      "transient_failed": 0, "retried_cached": 0}
        self._dirty = 0

    def __enter__(self) -> "CachedGeocoder":
        return self

    def __exit__(self, *exc) -> None:
        self.flush()

    def _call(self, provider: Provider, address: str) -> tuple[Optional[GeoResult], bool]:
        """(result, transient). transient=True means retries ran out on a transient error."""
        for attempt in range(self.max_retries + 1):
            try:
                return provider.lookup(address), False
            except TRANSIENT:
                if attempt == self.max_retries:
                    return None, True
                self.stats["retries"] += 1
                self.sleep(min(2 ** attempt, 30))
        return None, True  # pragma: no cover

    @staticmethod
    def _from_entry(entry: dict) -> Optional[GeoResult]:
        if "lat" not in entry:
            return None
        return GeoResult(entry["lat"], entry["lon"], entry["precision"])

    def _store(self, key: str, entry: dict) -> None:
        self.cache[key] = entry
        self._dirty += 1
        if self._dirty >= self.checkpoint_every:
            self.flush()

    def geocode(self, address: str) -> Optional[GeoResult]:
        key = normalize(address)
        entry = self.cache.get(key)
        if entry is not None and not entry.get("retry"):
            self.stats["cache_hit"] += 1
            return self._from_entry(entry)
        if entry is not None:
            self.stats["retried_cached"] += 1

        self.stats["api_call"] += 1
        res, transient = self._call(self.provider, address)
        if res is not None:
            self._store(key, {**res.__dict__, "retry": False})
            return res
        if entry is not None:
            # never overwrite: keep the stored fallback. Stop retrying if the primary now
            # says "not found" for sure.
            if not transient:
                self._store(key, {**entry, "retry": False})
            return self._from_entry(entry)
        if transient:
            self.stats["transient_failed"] += 1

        if self.fallback is not None:
            fres, ftransient = self._call(self.fallback, address)
            if fres is not None:
                self.stats["fallback"] += 1
                self._store(key, {**fres.__dict__, "retry": transient})
                return fres
            transient = transient or ftransient
        if transient:
            return None  # not stored, so the next run tries again
        self.stats["failed"] += 1
        self._store(key, {"status": "not_found", "retry": False})
        return None

    def geocode_many(self, addresses) -> list[Optional[GeoResult]]:
        """Geocode a list. The cache is flushed even if the run stops on an error."""
        try:
            return [self.geocode(a) for a in addresses]
        finally:
            self.flush()

    def flush(self) -> None:
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache, indent=0))
        tmp.replace(self.cache_path)  # atomic: a crash mid-write cannot corrupt the cache
        self._dirty = 0


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))
