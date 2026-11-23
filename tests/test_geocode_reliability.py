import json

import pytest

from territory.balance import UNASSIGNED, Account, books, spread
from territory.geocode import (CachedGeocoder, GeoResult, GoogleProvider, ProviderError, TransientError,
                               ZipCentroidProvider, normalize)

NO_SLEEP = dict(sleep=lambda s: None)


class ScriptedProvider:
    """Plays back a list of outcomes: an exception to raise, None, or a GeoResult."""

    def __init__(self, *outcomes, default=None):
        self.outcomes, self.default, self.calls = list(outcomes), default, 0

    def lookup(self, address):
        self.calls += 1
        out = self.outcomes.pop(0) if self.outcomes else self.default
        if isinstance(out, BaseException) or (isinstance(out, type) and issubclass(out, BaseException)):
            raise out
        return out


GOOD = GeoResult(40.0, -75.0, "rooftop")
ZIPS = ZipCentroidProvider({"78701": (30.27, -97.74)})


# ---------------- transient errors: retry with backoff, never crash the run
@pytest.mark.parametrize("err", [TimeoutError, ConnectionError, TransientError])
def test_transient_errors_are_retried(tmp_path, err):
    sleeps = []
    g = CachedGeocoder(ScriptedProvider(err(), err(), GOOD), tmp_path / "c.json", sleep=sleeps.append)
    assert g.geocode("1 Main St Austin TX 78701") == GOOD
    assert g.stats["retries"] == 2 and sleeps == [1, 2]


def test_transient_errors_after_max_retries_do_not_crash(tmp_path):
    g = CachedGeocoder(ScriptedProvider(default=TimeoutError()), tmp_path / "c.json", max_retries=2, **NO_SLEEP)
    assert g.geocode("1 Main St Austin TX 99999") is None
    assert g.stats["transient_failed"] == 1


def test_flush_happens_even_when_a_run_crashes(tmp_path):
    path = tmp_path / "c.json"
    p = ScriptedProvider(GOOD, GOOD, ProviderError("REQUEST_DENIED"))
    g = CachedGeocoder(p, path, checkpoint_every=50, **NO_SLEEP)
    with pytest.raises(ProviderError):
        g.geocode_many(["1 A St", "2 B St", "3 C St"])
    assert len(json.loads(path.read_text())) == 2


def test_context_manager_flushes_on_error(tmp_path):
    path = tmp_path / "c.json"
    with pytest.raises(RuntimeError):
        with CachedGeocoder(ScriptedProvider(GOOD), path, checkpoint_every=50, **NO_SLEEP) as g:
            g.geocode("1 A St")
            raise RuntimeError("boom")
    assert len(json.loads(path.read_text())) == 1


# ---------------- Google location_type is mapped, not hard-coded
class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, *payloads):
        self.payloads = list(payloads)

    def get(self, url, params=None, timeout=None):
        p = self.payloads.pop(0)
        if isinstance(p, BaseException):
            raise p
        return FakeResponse(p)


def _ok(location_type):
    return {"status": "OK", "results": [{"geometry": {"location": {"lat": 1.5, "lng": 2.5},
                                                      "location_type": location_type}}]}


@pytest.mark.parametrize("lt,precision", [("ROOFTOP", "rooftop"), ("RANGE_INTERPOLATED", "range_interpolated"),
                                          ("GEOMETRIC_CENTER", "geometric_center"), ("APPROXIMATE", "approximate"),
                                          (None, "unknown")])
def test_google_maps_location_type(lt, precision):
    gp = GoogleProvider(key="test", session=FakeSession(_ok(lt)))
    assert gp.lookup("x") == GeoResult(1.5, 2.5, precision)


def test_google_statuses():
    gp = GoogleProvider(key="test", session=FakeSession({"status": "ZERO_RESULTS", "results": []},
                                                        {"status": "UNKNOWN_ERROR"},
                                                        {"status": "REQUEST_DENIED"}))
    assert gp.lookup("x") is None
    with pytest.raises(TransientError):
        gp.lookup("x")
    with pytest.raises(ProviderError):
        gp.lookup("x")


def test_google_network_errors_become_transient():
    requests = pytest.importorskip("requests")
    gp = GoogleProvider(key="test", session=FakeSession(requests.Timeout("slow")))
    with pytest.raises(TransientError):
        gp.lookup("x")


# ---------------- fallback after a transient failure is cached with a retry flag
def test_transient_fallback_is_cached_and_retried_later(tmp_path):
    path = tmp_path / "c.json"
    addr = "1 Main St Austin TX 78701"
    g = CachedGeocoder(ScriptedProvider(default=TransientError("UNKNOWN_ERROR")), path,
                       fallback=ZIPS, max_retries=1, **NO_SLEEP)
    assert g.geocode(addr).precision == "zip_centroid"
    g.flush()
    assert json.loads(path.read_text())[normalize(addr)]["retry"] is True

    # next run: the primary works again, so the provisional fallback is upgraded
    g2 = CachedGeocoder(ScriptedProvider(GOOD), path, fallback=ZIPS, **NO_SLEEP)
    assert g2.geocode(addr) == GOOD
    assert g2.stats["retried_cached"] == 1
    assert g2.cache[normalize(addr)]["retry"] is False


def test_retry_that_fails_again_keeps_the_fallback(tmp_path):
    addr = "1 Main St Austin TX 78701"
    g = CachedGeocoder(ScriptedProvider(default=TimeoutError()), tmp_path / "c.json",
                       fallback=ZIPS, max_retries=0, **NO_SLEEP)
    first = g.geocode(addr)
    assert g.geocode(addr) == first and first.precision == "zip_centroid"


def test_permanent_not_found_fallback_is_not_retried(tmp_path):
    p = ScriptedProvider(default=None)  # like ZERO_RESULTS
    g = CachedGeocoder(p, tmp_path / "c.json", fallback=ZIPS, **NO_SLEEP)
    g.geocode("bad addr 78701")
    assert g.cache[normalize("bad addr 78701")]["retry"] is False
    g.geocode("bad addr 78701")
    assert p.calls == 1


def test_permanent_failure_is_cached_without_coordinates(tmp_path):
    p = ScriptedProvider(default=None)
    g = CachedGeocoder(p, tmp_path / "c.json", **NO_SLEEP)
    assert g.geocode("nowhere 00000") is None
    assert g.geocode("nowhere 00000") is None
    assert p.calls == 1 and "lat" not in g.cache[normalize("nowhere 00000")]


# ---------------- ZIP parsing prefers the trailing ZIP
def test_zip_prefers_trailing_zip_over_house_number():
    zp = ZipCentroidProvider({"12345": (1.0, 1.0), "78701": (30.27, -97.74)})
    assert zp.lookup("12345 Main St, Austin TX 78701").lat == 30.27
    assert zp.lookup("12345 Main St, Austin TX 78701-1234").lat == 30.27
    assert zp.lookup("12345 Main St, Austin TX 78701, USA").lat == 30.27
    assert zp.lookup("12345 Main St") is None


# ---------------- unassigned states are surfaced, not dropped
def test_unassigned_accounts_are_surfaced():
    terr = {"West": {"CA"}, "East": {"NY"}}
    accts = [Account("a", "CA", 100), Account("b", "NY", 50), Account("c", "KS", 70), Account("d", "", 5)]
    b = books(terr, accts)
    assert b[UNASSIGNED] == {"accounts": 2, "revenue": 75.0}
    assert sum(v["accounts"] for v in b.values()) == len(accts)
    assert spread(b) == 2.0  # the unassigned bucket is not a territory


# ---------------- review findings on PR #7
def test_zip_only_input_still_resolves():
    zp = ZipCentroidProvider({"78701": (30.27, -97.74)})
    assert zp.lookup("78701").lat == 30.27
    assert zp.lookup("78701, USA").lat == 30.27
    assert zp.lookup("78701-1234").lat == 30.27


def test_fallback_transient_failure_is_not_cached_as_not_found(tmp_path):
    primary = ScriptedProvider(default=None)                      # always "not found"
    fallback = ScriptedProvider(TimeoutError(), TimeoutError(), TimeoutError(), GOOD)
    g = CachedGeocoder(primary, tmp_path / "c.json", fallback=fallback, max_retries=2, **NO_SLEEP)
    assert g.geocode("somewhere 78701") is None                  # fallback timed out
    assert normalize("somewhere 78701") not in g.cache           # so nothing permanent is stored
    assert g.geocode("somewhere 78701") == GOOD                  # next call tries the fallback again
