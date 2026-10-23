"""Fix B: the nightly option-chain capture covers the active US list; provider exclusions are explicit."""
from datetime import date
from unittest.mock import patch

import src.services.scheduler as sched
import src.services.uw_option_chain as UW


def test_capture_list_is_leaps_plus_active_us_minus_exclusions():
    syms = sched._opthist_capture_symbols(us_symbols=["IONQ", "NET", "BRK-A", "MSFT", "0700.HK"])
    assert "IONQ" in syms and "NET" in syms and "QQQ" in syms          # US list + LEAPS list
    assert "BRK-A" not in syms and "0700.HK" not in syms and len(syms) == len(set(syms))
    leaps = [x for x in sched._OPTHIST_SYMBOLS if x != "BRK-A"]
    assert syms[:len(leaps)] == leaps                                   # income/LEAPS symbols captured first
    assert sched.opthist_provider_excluded("BRK-A").startswith("Unusual Whales returns 422")
    assert sched.opthist_provider_excluded("0700.HK") and sched.opthist_provider_excluded("NET") is None


def _daily(full_list):
    seen = {}
    import src.backtest.leaps_backtest as LB
    with patch.object(sched, "_is_us_trading_day", lambda *a: True), \
         patch.object(sched, "_opthist_capture_symbols", lambda: ["AAA", "NET"]), \
         patch.object(sched, "capture_option_chain_history", lambda syms, a, b, skip_existing: seen.setdefault("syms", syms) and {}), \
         patch.object(LB, "_symbol_usable_days", lambda *a: None), \
         patch.object(sched, "_record_job_status", lambda *a, **k: None):
        sched._capture_option_chain_history_daily(full_list=full_list)
    return seen["syms"]


def test_the_nightly_job_requests_the_full_list_and_a_startup_catch_up_only_leaps():
    assert _daily(True) == ["AAA", "NET"]
    assert _daily(False) == [x for x in sched._OPTHIST_SYMBOLS if x != "BRK-A"]
    src = (__import__("pathlib").Path(sched.__file__)).read_text()
    i = src.index("def _opthist_startup_check")
    assert "_capture_option_chain_history_daily(full_list=False)" in src[i:i + 3000]


def test_a_page_view_never_refetches_an_excluded_or_hk_symbol():
    def forbid(*a, **k): raise AssertionError("must not fetch an excluded symbol")
    with patch.object(UW, "latest_as_of", lambda *a, **k: None), \
         patch.object(sched, "capture_option_chain_history", forbid):
        for sym in ("BRK-A", "0700.HK"):
            out = UW.get_chain(None, sym, allow_fetch=True)
            assert out["available"] is False and out["reason"] == "provider_excluded" and out["reason_detail"]


def test_a_stale_supported_symbol_is_still_fetched_on_demand():
    called = []
    with patch.object(UW, "latest_as_of", lambda *a, **k: None), \
         patch.object(sched, "capture_option_chain_history", lambda *a, **k: called.append(a[0]) or {}), \
         patch.object(UW, "_release_read_transaction", lambda *a, **k: True):
        UW.get_chain(None, "NET", allow_fetch=True)
    assert called == [["NET"]]
