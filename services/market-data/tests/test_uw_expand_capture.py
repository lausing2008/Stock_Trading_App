"""AUD-UWEXPAND: tests for the three newly-wired UW capture paths.

Targets where the real risk lives, not the happy path:

  1. _uwx_date() must REFUSE free text. The FDA feed's `target_date` carries values like
     "2025-MID" and "2025-H2". Coercing those into a Date column would invent precision the
     source does not have; the schema stores them as TEXT and this parser must return None
     rather than guess.
  2. Falsy-zero discipline on flows. A net flow of exactly 0 (a real, common value on a quiet
     day) must survive as 0.0 and never collapse to None — the same bug class fixed repeatedly
     in this codebase.
  3. NEGATIVE values are the whole point. A negative change_premium means net REDEMPTION —
     money leaving the fund. Clamping or dropping it would erase half the signal.
"""
import datetime as dt

import src.services.scheduler as sch


# ── _uwx_date: the free-text refusal that protects the schema ────────────────────────────

def test_date_parses_a_plain_iso_date():
    assert sch._uwx_date("2026-09-03") == dt.date(2026, 9, 3)


def test_date_parses_a_full_timestamp():
    assert sch._uwx_date("2021-04-13T13:22:07Z") == dt.date(2021, 4, 13)


def test_date_refuses_the_fda_feeds_free_text_forms():
    """THE CORE GUARD. These are real values observed on the live FDA feed. Each must yield
    None so the row is stored with target_date_text intact and start_date NULL, rather than
    being coerced into a fabricated date."""
    for junk in ("2025-MID", "2025-H2", "2025-Q3", "MID-2025", "TBD", "", "   "):
        assert sch._uwx_date(junk) is None, f"{junk!r} must not parse as a date"


def test_date_refuses_non_strings():
    assert sch._uwx_date(None) is None
    assert sch._uwx_date(20260903) is None
    assert sch._uwx_date({}) is None


def test_date_accepts_a_longer_string_by_taking_the_date_prefix():
    """UW sometimes appends time/zone junk; the leading yyyy-mm-dd is still authoritative."""
    assert sch._uwx_date("2026-09-03 00:00:00") == dt.date(2026, 9, 3)


# ── Flow value coercion (shared _opthist_f) ──────────────────────────────────────────────

def test_flow_preserves_a_genuine_zero():
    """A quiet day with exactly zero net creation/redemption is real data, not absence."""
    assert sch._opthist_f(0) == 0.0
    assert sch._opthist_f("0") == 0.0


def test_flow_preserves_negative_values():
    """NEGATIVE = net redemption = money leaving. Half the signal lives on this side."""
    assert sch._opthist_f(-950000) == -950000.0
    assert sch._opthist_f("-732450000") == -732450000.0


def test_flow_parses_uws_string_numerics():
    """UW sends change_prem/close/marketcap as strings."""
    assert sch._opthist_f("732450000") == 732450000.0
    assert sch._opthist_f("773.17") == 773.17


def test_flow_returns_none_for_missing_and_malformed():
    assert sch._opthist_f(None) is None
    assert sch._opthist_f("n/a") is None


def test_capture_payloads_do_not_re_collapse_zero_via_an_or_default():
    """Guards the CALL SITES, not just the helper.

    _opthist_f() correctly preserves 0.0, but that is worthless if the payload construction
    then writes `_opthist_f(...) or None` — a real zero flow would still land as NULL. An
    earlier version of this file tested only the helper in isolation, and an adversarial
    `or None` added to change_premium passed all 19 tests. Assert on the source text of the
    numeric payload lines so that specific regression cannot return silently.
    """
    import inspect
    for fn in (sch.capture_etf_fund_flows,
               sch.capture_fda_catalysts,
               sch.capture_institutional_ownership):
        src = inspect.getsource(fn)
        for line in src.splitlines():
            if "_opthist_f(" in line:
                assert " or None" not in line and " or 0" not in line, (
                    f"{fn.__name__}: `{line.strip()}` re-collapses a genuine 0 — "
                    "_opthist_f already returns None for real absence"
                )


# ── Fetcher contracts: fail-open, list-returning ─────────────────────────────────────────

def _force_uw(monkeypatch, available=True, getter=None):
    import src.services.unusual_whales as uw
    monkeypatch.setattr(uw, "is_available", lambda: available)
    if getter is not None:
        monkeypatch.setattr(uw, "_get", getter)
    return uw


def test_etf_flow_fails_open_to_empty_list(monkeypatch):
    """Must never raise and never return None — a 403 from an entitlement change is caught
    exactly like a network error, so callers never branch on which failure occurred."""
    def boom(*a, **k):
        raise RuntimeError("simulated 403 / network failure")
    uw = _force_uw(monkeypatch, True, boom)
    assert uw.get_etf_fund_flow("SPY") == []


def test_fda_calendar_fails_open_to_empty_list(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    uw = _force_uw(monkeypatch, True, boom)
    assert uw.get_fda_calendar() == []


def test_inst_ownership_fails_open_to_empty_list(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    uw = _force_uw(monkeypatch, True, boom)
    assert uw.get_institutional_ownership("AAPL") == []


def test_fetchers_spend_no_request_when_uw_is_disabled(monkeypatch):
    calls = {"n": 0}

    def tracked(*a, **k):
        calls["n"] += 1
        return []
    uw = _force_uw(monkeypatch, False, tracked)
    assert uw.get_etf_fund_flow("SPY") == []
    assert uw.get_fda_calendar() == []
    assert uw.get_institutional_ownership("AAPL") == []
    assert uw.get_stock_screener() == []
    assert calls["n"] == 0, "must not spend requests when UW is unconfigured/disabled"


def test_fetchers_drop_non_dict_rows(monkeypatch):
    """One malformed row must not poison an entire day's capture."""
    uw = _force_uw(monkeypatch, True, lambda *a, **k: [{"date": "2026-09-03"}, "garbage", None])
    assert uw.get_etf_fund_flow("SPY") == [{"date": "2026-09-03"}]


def test_fetchers_tolerate_a_non_list_body(monkeypatch):
    """A dict/None body (error envelope) must degrade to [], not raise on iteration."""
    uw = _force_uw(monkeypatch, True, lambda *a, **k: {"error": "unexpected"})
    assert uw.get_etf_fund_flow("SPY") == []
    assert uw.get_fda_calendar() == []


# ── Universe / scheduling shape ──────────────────────────────────────────────────────────

def test_etf_universe_excludes_the_ticker_measured_as_empty():
    """ARKK returned zero rows when probed live, so it is deliberately excluded rather than
    left to fail and log noise every single day."""
    assert "ARKK" not in sch._ETF_FLOW_UNIVERSE


def test_etf_universe_covers_all_eleven_sector_spdrs():
    sectors = {"XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"}
    assert sectors <= set(sch._ETF_FLOW_UNIVERSE)


def test_etf_universe_includes_the_hk_relevant_names():
    """This platform trades HK, so FXI/EWH flows are directly relevant, not US-only decoration."""
    assert {"FXI", "EWH"} <= set(sch._ETF_FLOW_UNIVERSE)


def test_institutional_capture_has_no_whole_universe_default():
    """13F data changes quarterly; a daily whole-universe sweep would re-fetch unchanged data.
    The signature must therefore REQUIRE symbols rather than defaulting to None."""
    import inspect
    sig = inspect.signature(sch.capture_institutional_ownership)
    assert sig.parameters["symbols"].default is inspect.Parameter.empty
