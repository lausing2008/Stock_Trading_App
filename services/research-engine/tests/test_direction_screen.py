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
