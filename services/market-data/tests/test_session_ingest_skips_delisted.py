"""AUD-UW-DELISTED-INTRADAY: the five-minute session ingest passes skip confirmed-delisted symbols.

SKHYV and US10Y (both flagged delisted) were fetched 157 times each on 2026-11-02, failing on UW
(422) and yfinance every run. `_symbols_for()` must keep them (AUD-ING7-DELISTNEVERFIRES:
ingestion detects and reconfirms delisting; see test_delisted_excluded_from_scheduler_jobs.py),
so only the repeated session passes drop them and the post-close pass still fetches them.

scheduler.py cannot be imported here (see test_delisted_excluded_from_scheduler_jobs.py), so the
helper is extracted with `ast` and run against stand-ins; the call sites are checked as source.
"""
import ast
import pathlib
import types

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SOURCE = _PATH.read_text()


def _function_source(name: str) -> str:
    tree = ast.parse(_SOURCE)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(_SOURCE, node)
    raise AssertionError(f"{name} not found")


def _body(name: str) -> str:
    return _function_source(name)


class _Col:
    def is_(self, v):
        return ("is", v)

    def __eq__(self, other):  # Stock.market == market
        return ("eq", other)


def _helper(universe, delisted, *, fail=False):
    calls = {"universe": 0}

    class _Session:
        def __enter__(self):
            if fail:
                raise RuntimeError("db down")
            return self

        def __exit__(self, *a):
            return False

        def execute(self, _q):
            return types.SimpleNamespace(scalars=lambda: iter(delisted))

    class _Select:
        def where(self, *a):
            return self

    def symbols_for(market):
        calls["universe"] += 1
        return list(universe)

    ns = {
        "SessionLocal": _Session,
        "select": lambda *a: _Select(),
        "Stock": types.SimpleNamespace(symbol=_Col(), market=_Col(), delisted=_Col()),
        "_symbols_for": symbols_for,
        "log": types.SimpleNamespace(warning=lambda *a, **k: None),
    }
    exec(_function_source("_session_ingest_symbols"), ns)
    return ns["_session_ingest_symbols"], calls


def test_delisted_symbols_are_dropped_and_order_is_kept():
    fn, _ = _helper(["AAPL", "SKHYV", "NVDA", "US10Y"], ["SKHYV", "US10Y"])
    assert fn("US") == ["AAPL", "NVDA"]


def test_a_given_list_is_filtered_without_re_reading_the_universe():
    fn, calls = _helper(["IGNORED"], ["SKHYV"])
    assert fn("US", ["SKHYV", "MU"]) == ["MU"]
    assert calls["universe"] == 0


def test_no_delisted_symbols_returns_the_universe_unchanged():
    fn, _ = _helper(["AAPL", "NVDA"], [])
    assert fn("US") == ["AAPL", "NVDA"]


def test_an_unreadable_flag_drops_nothing():
    fn, _ = _helper(["AAPL", "SKHYV"], ["SKHYV"], fail=True)
    assert fn("US") == ["AAPL", "SKHYV"]


@pytest.mark.parametrize("job", ["_refresh_5m", "_refresh_premarket_5m"])
def test_session_5m_passes_use_the_filtered_universe(job):
    body = _body(job)
    assert "_session_ingest_symbols(" in body
    assert "_symbols_for(" not in body


def test_5m_reconcile_uses_the_filtered_universe():
    src = _SOURCE[_SOURCE.index('key = f"scheduler:job:{market.lower()}_5m_reconcile:detail"') - 300:]
    assert src.index("_session_ingest_symbols(market)") < src.index("_5m_reconcile:detail")


def test_post_close_daily_pass_keeps_delisted_symbols_for_detection():
    body = _body("_refresh_market")
    assert 'ingest_universe(symbols if post_close else _session_ingest_symbols(market, symbols), "1d")' in body
    assert "symbols = _symbols_for(market)" in body


def test_weekly_forced_refresh_still_ingests_everything():
    assert 'ingest_universe(all_symbols, "1d", force=True)' in _SOURCE
