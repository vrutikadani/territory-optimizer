import pytest

from territory import naming
from territory.balance import Account, books, spread, suggest_moves
from territory.geocode import CachedGeocoder, GeoResult, RateLimited, ZipCentroidProvider, haversine_miles, normalize
from territory.routing import Contact, Router


# ---------------- geocoding
class FlakyProvider:
    def __init__(self, fail_times=2):
        self.calls, self.fail_times = 0, fail_times

    def lookup(self, address):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RateLimited()
        return None if "BAD" in address.upper() else GeoResult(40.0, -75.0, "rooftop")


def test_retry_then_cache(tmp_path):
    p = FlakyProvider(fail_times=2)
    g = CachedGeocoder(p, tmp_path / "c.json", sleep=lambda s: None)
    assert g.geocode("1 Main Street, Austin TX 78701").precision == "rooftop"
    assert g.stats["retries"] == 2
    g.geocode("1 MAIN ST AUSTIN TX 78701")  # same address, different spelling
    assert g.stats["cache_hit"] == 1 and p.calls == 3


def test_fallback_to_zip_and_no_overwrite_on_failure(tmp_path):
    zipp = ZipCentroidProvider({"78701": (30.27, -97.74)})
    g = CachedGeocoder(FlakyProvider(0), tmp_path / "c.json", fallback=zipp, sleep=lambda s: None)
    assert g.geocode("BAD addr 78701").precision == "zip_centroid"
    assert g.geocode("BAD addr 99999") is None
    assert normalize("BAD addr 99999") not in g.cache


def test_checkpoint_survives_restart(tmp_path):
    path = tmp_path / "c.json"
    g = CachedGeocoder(FlakyProvider(0), path, checkpoint_every=2, sleep=lambda s: None)
    g.geocode("1 A St 10001"); g.geocode("2 B St 10001")  # triggers flush
    g2 = CachedGeocoder(FlakyProvider(0), path)
    assert len(g2.cache) == 2


def test_haversine():
    assert haversine_miles((40.71, -74.0), (34.05, -118.24)) == pytest.approx(2445, rel=0.01)


# ---------------- naming
def test_naming_convention():
    tid, name = naming.build(["MS", "AL"], ["MEM", "Atlanta"])
    assert tid == "MS, AL, Memphis, Atlanta"
    assert name == "Mississippi, Alabama, Memphis, Atlanta"


def test_lint_flags_rep_names_temp_and_airports():
    probs = naming.lint("Temp JORDAN / LAX", {"Jordan Blake"})
    assert len(probs) == 3


# ---------------- routing
@pytest.fixture
def router():
    return Router({"10001": "Outside-NY", "60601": "Outside-IL"}, ["Inside-1", "Inside-2"])


def test_non_medical_mql_goes_inside(router):
    assert router.route(Contact("c", "co", "10001", "Chiropractic")).rule.startswith("2")


def test_med_spa_without_director_goes_inside(router):
    assert router.route(Contact("c", "co", "10001", "Med Spa", has_medical_director=False)).rule.startswith("3")


def test_dermatology_goes_to_covering_outside_rep(router):
    assert router.route(Contact("c", "co", "60601", "Dermatology")).owner == "Outside-IL"


def test_no_coverage_falls_back_inside(router):
    assert router.route(Contact("c", "co", "94105", "Dermatology")).rule.startswith("5")


def test_manual_handoff_is_never_overwritten(router):
    c = Contact("c", "co", "60601", "Dermatology", owner="Inside-2", manual_owner_lock=True)
    assert router.route(c).owner == "Inside-2"


def test_company_follows_contacts(router):
    out = router.route_all([Contact("c1", "co1", "60601", "Dermatology")])
    assert out["company:co1"].owner == "Outside-IL"


# ---------------- balance
def test_suggest_moves_reduces_spread():
    terr = {"West": {"CA", "NV", "AZ"}, "Mountain": {"UT", "CO"}}
    accts = [Account("a", "CA", 900), Account("b", "NV", 300), Account("c", "AZ", 300),
             Account("d", "UT", 100), Account("e", "CO", 100)]
    before = spread(books(terr, accts))
    moves = suggest_moves(terr, accts)
    assert moves and moves[-1]["spread_after"] < before
