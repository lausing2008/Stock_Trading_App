"""AUD-EARNSURPRISE-STOCK — a stock's own earnings history as EVIDENCE, not a rate.

THE MEASUREMENT THAT SHAPED THIS. Across 130 symbols with usable data the MEDIAN is 5 earnings
events per symbol (mean 4.8, max 9); only 6 symbols have 8 or more, and beat-only counts are
smaller still. This module enforces a 30-beat floor before it will rank a SECTOR, precisely
because "(unclassified)" showed a +47.41pp spread on two beats. A per-stock "expected drift"
computed on n=2-5 clears no honest bar — it is noise wearing a percent sign, and the more
confident it looks the more misleading it is.

So the contract under test is: raw events are always returned, directional beat consistency is
returned (robust where magnitude is not), the SECTOR base rate is carried through as the
statistically-supported figure, and any per-stock average is gated behind an explicit
`stock_drift_is_statistically_usable` flag that is False below 8 big beats.
"""
from datetime import date
from unittest.mock import MagicMock

import src.services.earnings as e


class _Stock:
    def __init__(self, sector="Industrials"):
        self.id = 1
        self.symbol = "AAA"
        self.sector = sector
        self.market = "US"


class _Ev:
    """`drift5` is a FRACTION, matching the real column (0.10 = +10%). Passing a percent here
    silently inflates every assertion by 100x — the same fraction/percent trap that made
    post_earnings_return_1d look broken during the audit."""
    def __init__(self, d, surprise, drift5=None):
        self.report_date = d
        self.surprise_pct = surprise
        self.revenue_surprise_pct = None
        self.eps_actual = 1.0
        self.eps_estimate = 0.8
        self.post_earnings_return_1d = None
        self.post_earnings_return_5d = drift5


def _sector(name="Industrials", adequate=True):
    return {"sector": name, "n_total": 89, "n_beats": 50, "beat_drift_pct": 9.91,
            "nonbeat_drift_pct": -1.50, "beat_drift_after_open_pct": 5.55,
            "spread_pp": 11.41, "sample_is_adequate": adequate}


def _run(monkeypatch, events, stock=None, beat_rate=0.75, sectors=None):
    stock = stock or _Stock()
    sess = MagicMock()
    sess.__enter__.return_value = sess
    sess.__exit__.return_value = False
    sess.execute.return_value.first.return_value = stock
    sess.execute.return_value.all.return_value = events
    monkeypatch.setattr(e, "SessionLocal", lambda: sess)
    monkeypatch.setattr(e, "select", lambda *a, **k: MagicMock())
    monkeypatch.setattr(e, "Stock", MagicMock())
    monkeypatch.setattr(e, "EarningsEvent", MagicMock())
    monkeypatch.setattr(e, "get_beat_rate", lambda *_a, **_k: beat_rate)
    monkeypatch.setattr(e, "get_earnings_surprise_impact",
                        lambda *_a, **_k: {"by_sector": sectors if sectors is not None else [_sector()]})
    return e.get_stock_earnings_history("AAA")


_D = date(2026, 8, 1)


# ── The gate that stops a 5-event sample masquerading as a rate ──────────────────────────────

def test_a_typical_symbol_sample_is_flagged_unusable(monkeypatch):
    """The median symbol has 5 TOTAL events. Three big beats must NOT yield a usable rate."""
    evs = [_Ev(_D, 20.0, 0.03), _Ev(_D, 15.0, -0.01), _Ev(_D, 30.0, 0.08)]
    r = _run(monkeypatch, evs)
    assert r["n_big_beats"] == 3
    assert r["stock_drift_is_statistically_usable"] is False


def test_the_average_is_still_returned_so_it_can_be_shown_caveated(monkeypatch):
    """Withholding it entirely would just push callers to compute their own, uncaveated."""
    evs = [_Ev(_D, 20.0, 0.04), _Ev(_D, 15.0, 0.02)]
    r = _run(monkeypatch, evs)
    assert r["stock_avg_drift_pct"] == 3.0
    assert r["stock_drift_is_statistically_usable"] is False


def test_eight_big_beats_clears_the_bar(monkeypatch):
    evs = [_Ev(_D, 20.0, 0.03) for _ in range(8)]
    r = _run(monkeypatch, evs)
    assert r["n_big_beats"] == 8
    assert r["stock_drift_is_statistically_usable"] is True


def test_seven_does_not(monkeypatch):
    evs = [_Ev(_D, 20.0, 0.03) for _ in range(7)]
    r = _run(monkeypatch, evs)
    assert r["stock_drift_is_statistically_usable"] is False


# ── Raw events are the product ───────────────────────────────────────────────────────────────

def test_every_event_is_returned_individually(monkeypatch):
    evs = [_Ev(_D, 20.0, 0.03), _Ev(_D, -14.0, -0.05), _Ev(_D, 2.0, 0.01)]
    r = _run(monkeypatch, evs)
    assert r["n_events"] == 3
    assert [x["surprise_pct"] for x in r["events"]] == [20.0, -14.0, 2.0]


def test_big_beat_flag_respects_the_threshold(monkeypatch):
    evs = [_Ev(_D, 20.0, 0.03), _Ev(_D, 4.0, 0.01), _Ev(_D, -30.0, -0.04)]
    r = _run(monkeypatch, evs)
    assert [x["was_big_beat"] for x in r["events"]] == [True, False, False]


def test_only_big_beats_feed_the_average(monkeypatch):
    """A small beat and a miss must not dilute a big-beat drift figure."""
    evs = [_Ev(_D, 20.0, 0.10), _Ev(_D, 3.0, -0.50), _Ev(_D, -40.0, -0.50)]
    r = _run(monkeypatch, evs)
    assert r["n_big_beats"] == 1
    assert r["stock_avg_drift_pct"] == 10.0


def test_events_without_drift_data_do_not_poison_the_average(monkeypatch):
    evs = [_Ev(_D, 20.0, 0.06), _Ev(_D, 25.0, None)]
    r = _run(monkeypatch, evs)
    assert r["n_big_beats"] == 1
    assert r["stock_avg_drift_pct"] == 6.0


# ── Beat consistency and sector context ──────────────────────────────────────────────────────

def test_beat_consistency_comes_from_the_existing_shared_helper(monkeypatch):
    """Reuses get_beat_rate(), which SA-7's compression already depends on — a second,
    divergent definition of 'beat rate' in the same codebase would be worse than none."""
    r = _run(monkeypatch, [_Ev(_D, 20.0, 0.03)], beat_rate=0.875)
    assert r["beat_consistency"] == 0.875


def test_sector_base_rate_is_carried_through_with_its_adequacy_flag(monkeypatch):
    r = _run(monkeypatch, [_Ev(_D, 20.0, 0.03)])
    assert r["sector_base_rate"]["sample_is_adequate"] is True
    assert r["sector_base_rate"]["beat_drift_after_open_pct"] == 5.55


def test_a_thin_sector_keeps_its_false_adequacy_flag(monkeypatch):
    r = _run(monkeypatch, [_Ev(_D, 20.0, 0.03)], sectors=[_sector(adequate=False)])
    assert r["sector_base_rate"]["sample_is_adequate"] is False


def test_a_stock_whose_sector_has_no_row_gets_none_not_a_crash(monkeypatch):
    r = _run(monkeypatch, [_Ev(_D, 20.0, 0.03)], sectors=[_sector("Technology")])
    assert r["sector_base_rate"] is None


# ── Edges ────────────────────────────────────────────────────────────────────────────────────

def test_unknown_symbol_returns_found_false_rather_than_raising(monkeypatch):
    sess = MagicMock()
    sess.__enter__.return_value = sess
    sess.__exit__.return_value = False
    sess.execute.return_value.first.return_value = None
    monkeypatch.setattr(e, "SessionLocal", lambda: sess)
    monkeypatch.setattr(e, "select", lambda *a, **k: MagicMock())
    monkeypatch.setattr(e, "Stock", MagicMock())
    r = e.get_stock_earnings_history("NOPE")
    assert r["found"] is False and r["events"] == []


def test_a_symbol_with_no_earnings_history_is_not_an_error(monkeypatch):
    r = _run(monkeypatch, [])
    assert r["n_events"] == 0
    assert r["stock_avg_drift_pct"] is None
    assert r["stock_drift_is_statistically_usable"] is False


def test_the_gap_caveat_is_stated(monkeypatch):
    """drift_5d_pct here INCLUDES the overnight gap, unlike the sector view's after-open
    figures. Mixing the two silently would make a stock look better than its sector."""
    r = _run(monkeypatch, [_Ev(_D, 20.0, 0.03)])
    assert any("gap" in c.lower() for c in r["caveats"])
