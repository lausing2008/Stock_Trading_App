import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from intel_reports.direction_screen import assess, select_setups


def sample(close=100):
    bars = [{"date": f"session-{i:02}", "high": 105, "low": 95,
             "close": 100, "volume": 1000, "adj_close": 100} for i in range(21)]
    bars[-1].update(close=close, high=max(105, close), low=min(95, close), adj_close=close)
    return bars, [b["date"] for b in bars]


@pytest.mark.parametrize("close,direction", [(106, "breakout"), (94, "breakdown"),
    (104, "breakout_watch"), (96, "breakdown_watch"), (100, "range")])
def test_direction_and_levels_exclude_latest_bar(close, direction):
    bars, dates = sample(close)
    result = assess(bars, dates)
    assert result["direction"] == direction
    assert result["support"] == 95 and result["resistance"] == 105
    assert "probability" not in result


@pytest.mark.parametrize("defect", ["missing", "stale", "duplicate", "zero", "nan", "split"])
def test_bad_evidence_is_unknown_not_range(defect):
    bars, dates = sample()
    if defect == "missing": bars.pop()
    if defect == "stale": bars[-1]["date"] = "old"
    if defect == "duplicate": bars[-1]["date"] = bars[-2]["date"]
    if defect == "zero": bars[-1]["volume"] = 0
    if defect == "nan": bars[-1]["close"] = float("nan")
    if defect == "split": bars[0]["adj_close"] = 50
    assert assess(bars, dates)["direction"] == "unknown"


def test_missing_adjustment_provenance_disclosed():
    bars, dates = sample()
    for b in bars: b["adj_close"] = None
    assert "incomplete" in assess(bars, dates)["limitations"][0]


def test_filter_before_limit_and_rank_separately_per_market():
    rows = [{"symbol": f"{market}{i:03}", "market": market, "sector": "Tech",
             "setup": {"direction": "breakout", "volume_ratio": i + 1,
                       "distance_pct": 1}} for market in ("US", "HK") for i in range(30)]
    chosen = select_setups(rows)
    assert len(chosen) == 40
    assert chosen[0]["symbol"] == "US029" and chosen[20]["symbol"] == "HK029"
    assert len(select_setups(rows, "HK", "breakout", 20, "Tech")) == 20
    assert select_setups(rows, "US", "breakdown") == []
    assert select_setups(rows, sector="Bank") == []


# ---- instrument type: the shortlist is not all operating companies -------------------------
# The screen visibly returns QQQM and QLD. There is no `is_etf` column, so the type is inferred
# — and the precedence below is fixed by measurement against the 189 active listings on
# 2026-10-07, not by preference.

from intel_reports.direction_screen import classify_instrument  # noqa: E402


def _c(symbol, name, sector=None, industry=None, stmts=False):
    return classify_instrument(symbol=symbol, name=name, sector=sector,
                               industry=industry, has_annual_statements=stmts)


def test_stored_statements_identify_an_operating_company():
    """0 of 189 listings carry annual statements without a sector, so this signal led with no
    observed counterexample."""
    r = _c("MU", "Micron Technology, Inc.", sector="Technology", stmts=True)
    assert r["type"] == "operating_company"
    assert "annual financial statements are stored" in r["basis"]


def test_a_company_without_statements_is_not_mislabelled_as_a_fund():
    """MEASURED: 7 listings have no statements but do carry a sector — AMD, AMGN, ASTS, COHR,
    COIN, MXL, TXN. Using "no statements" alone would have called all seven funds."""
    for sym, name in (("AMD", "Advanced Micro Devices, Inc."), ("TXN", "Texas Instruments"),
                      ("COIN", "Coinbase Global, Inc.")):
        r = _c(sym, name, sector="Technology", stmts=False)
        assert r["type"] == "operating_company", sym
        assert "no statements stored yet" in r["basis"]


def test_a_fund_is_identified_from_its_name_when_nothing_else_can():
    for sym, name in (("QQQM", "Invesco NASDAQ 100 ETF"), ("GLD", "SPDR Gold Shares"),
                      ("DIA", "State Street SPDR Dow Jones Industrial Average ETF Trust"),
                      ("GDX", "VanEck Gold Miners ETF")):
        assert _c(sym, name)["type"] == "fund", sym


def test_a_daily_reset_leveraged_fund_is_flagged_as_one():
    """§30 of the intelligence spec: SOXL must not be treated like an ordinary 1x ETF."""
    for sym, name in (("QLD", "ProShares Ultra QQQ"),
                      ("TQQQ", "ProShares UltraPro QQQ"),
                      ("SOXL", "Direxion Daily Semiconductor Bull 3X Shares")):
        r = _c(sym, name)
        assert r["type"] == "fund" and r["leveraged"] is True, sym
        assert "daily-reset multiple" in r["basis"]


def test_an_ordinary_fund_is_not_flagged_as_leveraged():
    assert _c("QQQM", "Invesco NASDAQ 100 ETF")["leveraged"] is False


def test_an_unresolved_identity_is_unverified_rather_than_guessed():
    """AAOI, CBRS, HOOD and MUU have no statements, no sector and a name equal to the symbol.
    They are real companies with unresolved names; calling them funds would be a fabrication
    and calling them companies would be a guess."""
    for sym in ("HOOD", "AAOI", "CBRS", "MUU"):
        r = _c(sym, sym)
        assert r["type"] == "unverified", sym
        assert "not established either way" in r["basis"]


def test_every_classification_states_the_evidence_behind_it():
    for r in (_c("MU", "Micron", sector="Technology", stmts=True),
              _c("QQQM", "Invesco NASDAQ 100 ETF"), _c("HOOD", "HOOD")):
        assert r["basis"] and len(r["basis"]) > 20
        assert set(r) == {"type", "leveraged", "basis"}


def test_only_three_types_exist_so_a_reader_never_sees_a_blank():
    for r in (_c("X", None), _c("Y", ""), _c("Z", "Z"), _c("W", "Something Corp")):
        assert r["type"] in ("operating_company", "fund", "unverified")
