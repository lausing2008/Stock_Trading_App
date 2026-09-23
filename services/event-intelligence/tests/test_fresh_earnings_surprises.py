"""AUD-EARNSURPRISE-ALERT — fresh earnings surprises, annotated to be actionable.

"NVDA beat by 14%" is not actionable on its own. Two things make it so, and both are tested
here because both are easy to quietly lose:

  1. THE SECTOR BASE RATE WITH ITS SAMPLE SIZE. Measured post-announcement drift differs
     enormously by sector — Industrials beats drift +5.55% over 5 days (n=89, 50 beats) while
     Consumer Cyclical beats are negative. A sector below the 30-beat floor must never count as
     actionable: "(unclassified)" shows a +47.41pp spread on TWO beats.
  2. TIME DECAY, STATED. The +4.19% edge is measured over 5 trading days FROM THE REPORT. A
     surprise surfaced 4 days later has ~1 day of that window left, and a fully-elapsed one has
     none — the drift already happened. Without this a stale row looks as good as a fresh one.

Drift figures are post-announcement only. An alert fires once the result is public, so the
overnight gap is already gone; quoting the gap-inclusive number would advertise a return this
alert cannot reach (Communication Services: +4.66% incl. gap, -0.10% after it).

See docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

import src.services.earnings as e


class _Row:
    def __init__(self, symbol, sector, report_date, surprise_pct, market="US"):
        self.symbol = symbol
        self.sector = sector
        self.market = market
        self.report_date = report_date
        self.surprise_pct = surprise_pct
        self.revenue_surprise_pct = None
        self.eps_actual = 1.0
        self.eps_estimate = 0.8
        self.post_earnings_return_1d = None


def _impact(sectors):
    return {
        "overall": {"beat": {"drift_after_open_pct": 4.19}},
        "by_sector": sectors,
    }


def _sector(name, adequate=True, n_beats=50):
    return {
        "sector": name, "n_total": 89, "n_beats": n_beats,
        "beat_drift_pct": 9.91, "nonbeat_drift_pct": -1.50,
        "beat_drift_after_open_pct": 5.55, "spread_pp": 11.41,
        "sample_is_adequate": adequate,
    }


class _FakeColumn:
    """`db` is stubbed in this service's test env, so the real models are MagicMocks and
    `EarningsEvent.report_date >= cutoff` raises. Same fake-plumbing approach
    test_post_earnings_returns.py already established for this module."""
    def __ge__(self, other): return True
    def __le__(self, other): return True
    def is_(self, other): return True
    def isnot(self, other): return True
    def desc(self): return self
    def __eq__(self, other): return True


class _FakeEvent:
    stock_id = report_date = surprise_pct = revenue_surprise_pct = _FakeColumn()
    eps_actual = eps_estimate = post_earnings_return_1d = _FakeColumn()


class _FakeStock:
    id = symbol = sector = market = active = delisted = _FakeColumn()


class _Stmt:
    def join(self, *a, **k): return self
    def where(self, *a, **k): return self
    def order_by(self, *a, **k): return self


def _run(monkeypatch, rows, sectors, **kw):
    monkeypatch.setattr(e, "get_earnings_surprise_impact", lambda *_a, **_k: _impact(sectors))
    monkeypatch.setattr(e, "select", lambda *a, **k: _Stmt())
    monkeypatch.setattr(e, "EarningsEvent", _FakeEvent)
    monkeypatch.setattr(e, "Stock", _FakeStock)
    sess = MagicMock()
    sess.__enter__.return_value = sess
    sess.__exit__.return_value = False
    sess.execute.return_value.all.return_value = rows
    monkeypatch.setattr(e, "SessionLocal", lambda: sess)
    return e.get_fresh_earnings_surprises(**kw)


_TODAY = date.today()


# ── Threshold ────────────────────────────────────────────────────────────────────────────────

def test_below_threshold_surprises_are_excluded(monkeypatch):
    r = _run(monkeypatch, [_Row("AAA", "Industrials", _TODAY, 4.0)], [_sector("Industrials")])
    assert r["n_total"] == 0


def test_a_large_miss_is_included_as_context_but_never_actionable(monkeypatch):
    """Misses matter for situational awareness; the measured edge is a BEAT effect."""
    r = _run(monkeypatch, [_Row("AAA", "Industrials", _TODAY, -25.0)], [_sector("Industrials")])
    assert r["n_total"] == 1
    assert r["surprises"][0]["direction"] == "miss"
    assert r["n_actionable"] == 0


# ── Time decay — the part that stops a stale row looking fresh ───────────────────────────────

def test_a_same_day_surprise_has_the_full_window(monkeypatch):
    r = _run(monkeypatch, [_Row("AAA", "Industrials", _TODAY, 14.0)], [_sector("Industrials")])
    s = r["surprises"][0]
    assert s["days_elapsed"] == 0
    assert s["window_remaining_days"] == 5
    assert s["window_remaining_pct"] == 100


def test_window_decays_with_elapsed_days(monkeypatch):
    r = _run(monkeypatch,
             [_Row("AAA", "Industrials", _TODAY - timedelta(days=4), 14.0)],
             [_sector("Industrials")])
    s = r["surprises"][0]
    assert s["days_elapsed"] == 4
    assert s["window_remaining_days"] == 1
    assert s["window_remaining_pct"] == 20


def test_a_fully_elapsed_window_is_not_actionable(monkeypatch):
    """The historical drift has already happened — surfacing it as a call would be selling a
    return that is no longer available."""
    r = _run(monkeypatch,
             [_Row("AAA", "Industrials", _TODAY - timedelta(days=6), 30.0)],
             [_sector("Industrials")], lookback_days=10)
    s = r["surprises"][0]
    assert s["window_remaining_days"] == 0
    assert s["window_remaining_pct"] == 0
    assert r["n_actionable"] == 0, "a fully-decayed surprise must not count as actionable"


def test_window_never_goes_negative(monkeypatch):
    r = _run(monkeypatch,
             [_Row("AAA", "Industrials", _TODAY - timedelta(days=30), 30.0)],
             [_sector("Industrials")], lookback_days=30)
    assert r["surprises"][0]["window_remaining_days"] == 0


# ── The sample floor gates actionability ─────────────────────────────────────────────────────

def test_a_thin_sector_is_reported_but_never_actionable(monkeypatch):
    """'(unclassified)' shows a +47.41pp spread on TWO beats. Reporting it is fine; counting it
    as a call is not."""
    r = _run(monkeypatch, [_Row("AAA", "Energy", _TODAY, 22.0)],
             [_sector("Energy", adequate=False, n_beats=2)])
    assert r["n_total"] == 1
    assert r["surprises"][0]["sector_base_rate"]["sample_is_adequate"] is False
    assert r["n_actionable"] == 0


def test_an_adequate_sector_beat_inside_the_window_is_actionable(monkeypatch):
    r = _run(monkeypatch, [_Row("AAA", "Industrials", _TODAY, 14.0)], [_sector("Industrials")])
    assert r["n_actionable"] == 1
    assert r["surprises"][0]["sector_base_rate"]["beat_drift_after_open_pct"] == 5.55


def test_a_sector_with_no_context_row_does_not_crash_or_count(monkeypatch):
    r = _run(monkeypatch, [_Row("AAA", "Utilities", _TODAY, 14.0)], [_sector("Industrials")])
    assert r["surprises"][0]["sector_base_rate"] is None
    assert r["n_actionable"] == 0


# ── Ordering and the post-announcement convention ────────────────────────────────────────────

def test_freshest_and_largest_surface_first(monkeypatch):
    rows = [
        _Row("OLD", "Industrials", _TODAY - timedelta(days=4), 40.0),
        _Row("BIG", "Industrials", _TODAY, 30.0),
        _Row("SML", "Industrials", _TODAY, 12.0),
    ]
    r = _run(monkeypatch, rows, [_sector("Industrials")])
    assert [x["symbol"] for x in r["surprises"]] == ["BIG", "SML", "OLD"]


def test_the_headline_drift_is_the_post_announcement_figure(monkeypatch):
    """Must be the after-open number (4.19), not the gap-inclusive one (5.90) — an alert fires
    after the result is public and cannot capture the gap."""
    r = _run(monkeypatch, [_Row("AAA", "Industrials", _TODAY, 14.0)], [_sector("Industrials")])
    assert r["overall_beat_drift_after_open_pct"] == 4.19


def test_caveats_are_returned_for_the_ui_to_render(monkeypatch):
    r = _run(monkeypatch, [], [])
    joined = " ".join(r["caveats"]).lower()
    assert "gap" in joined and "sample_is_adequate" in joined
