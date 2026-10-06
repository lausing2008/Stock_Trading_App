"""The rates adapter, against the production row shape.

WHY THIS FILE EXISTS. A sabotage run removed the adapter's non-null filter and every test
still passed — the driver tests all used a hand-built reading dict and never exercised the code
that BUILDS it. The NULL handling is the part most likely to be wrong and was the part with no
coverage.

The shape is taken from production on 2026-10-05: the 2026-10-02 row carried a term spread and
nothing else, while the yields were last observed on 2026-10-01.
"""
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from intel_reports.rate_readings import build_readings  # noqa: E402


def _row(d, **kw):
    return SimpleNamespace(
        as_of=d, fetched_at=datetime(d.year, d.month, d.day, 1, 0),
        yield_2y=kw.get("y2"), yield_10y=kw.get("y10"),
        yield_curve_2s10s=kw.get("s"), hy_spread=kw.get("hy"), dxy=kw.get("dxy"))


def rates_value(rows):
    """What the adapter produces once its query has run."""
    return build_readings(rows, lookback_days=30)


#: Exactly production's shape: a partial newest row above fully-populated earlier ones.
PROD = [
    _row(date(2026, 10, 2), s=0.45),
    _row(date(2026, 10, 1), y2=4.78, y10=5.24, s=0.46, hy=3.24),
    _row(date(2026, 9, 30), y2=4.88, y10=5.29, s=0.41, hy=3.12),
]
CUTOFF = datetime(2026, 10, 5)


def test_each_series_reports_its_own_observation_date():
    """Taking the newest row's date for every series would stamp a 1 October yield with a
    2 October timestamp."""
    v = rates_value(PROD)["readings"]
    assert v["yield_10y"]["observation_date"] == "2026-10-01"
    assert v["yield_curve_2s10s"]["observation_date"] == "2026-10-02"


def test_a_null_is_never_compared_as_a_value():
    """The defect a sabotage run exposed: without the non-null filter the 10-year's latest
    reading becomes None and its change is computed against a missing number."""
    v = rates_value(PROD)["readings"]
    y10 = v["yield_10y"]
    assert y10["latest"] == 5.24
    assert y10["previous"] == 5.29
    assert y10["change_bp"] == -5.0


def test_the_term_spread_compares_the_two_days_it_was_actually_observed():
    v = rates_value(PROD)["readings"]
    s = v["yield_curve_2s10s"]
    assert (s["observation_date"], s["previous_observation_date"]) == ("2026-10-02",
                                                                       "2026-10-01")
    assert s["change_bp"] == -1.0


def test_a_series_never_observed_is_not_observed_not_zero():
    v = rates_value(PROD)["readings"]
    assert v["dxy"]["status"] == "NOT OBSERVED"
    assert "not an unchanged one" in v["dxy"]["note"]
    assert "latest" not in v["dxy"] and "change" not in v["dxy"]


def test_one_observation_yields_no_change_rather_than_a_zero_change():
    v = rates_value([_row(date(2026, 10, 1), y10=5.24)])["readings"]
    y10 = v["yield_10y"]
    assert y10["latest"] == 5.24
    assert y10["change"] is None
    assert "no change is measured" in y10["note"]


def test_every_reading_names_its_series_and_tenor():
    v = rates_value(PROD)["readings"]
    assert v["yield_10y"]["series"] == "DGS10" and v["yield_10y"]["tenor"] == "10-year"
    assert v["yield_2y"]["series"] == "DGS2" and v["yield_2y"]["tenor"] == "2-year"
    assert v["hy_spread"]["series"] == "BAMLH0A0HYM2"
    assert v["yield_curve_2s10s"]["series"] == "T10Y2Y"


def test_retrieval_time_is_recorded_separately_from_the_observation_date():
    v = rates_value(PROD)["readings"]
    y10 = v["yield_10y"]
    assert y10["observation_date"] == "2026-10-01"
    assert y10["retrieved_at"].startswith("2026-10-01T01:00")


def test_no_rows_is_refused_by_the_adapter_before_building_anything():
    """The emptiness check lives in the adapter, beside the query that produced it."""
    src = (Path(__file__).resolve().parents[1] / "src" / "intel_reports"
           / "adapters.py").read_text()
    seg = src[src.index("def rates(session"):]
    seg = seg[:seg.index("return calculated")]
    assert "if not rows:" in seg
    assert "no cross-asset reading is stored" in seg


def test_the_field_states_that_these_are_market_prices_not_policy():
    v = rates_value(PROD)
    assert "MARKET-PRICED" in v["scope"]
    assert "NOT a policy decision" in v["scope"]
