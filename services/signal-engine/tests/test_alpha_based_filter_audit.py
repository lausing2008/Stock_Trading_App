"""AUD-ALPHAEVAL (2026-09-22) — filter_audit() now reports benchmark-relative alpha.

Every verdict this endpoint produced was computed on ABSOLUTE return, which credits a BUY
simply for firing in a rising market and blames one for firing in a falling market. A filter's
"harmful"/"predictive" label therefore partly measured market drift rather than the filter.

Measured over the same 180-day window, the distinction is the whole story: BUY signals returned
-1.11% while SPY over each signal's OWN matched window returned -0.03%, so the real selection
effect is -1.08pp. And the benchmark must be PER MARKET: re-running the HK population against
2800.HK instead of SPY moved HK alpha from -5.56% to -6.71% — the wrong benchmark was
FLATTERING it, not penalising it.

Two deliberate design properties these tests lock in:

1. **A missing benchmark yields None, never 0.0.** Treating an unavailable benchmark as a flat
   market would report the raw return AS IF it were alpha — silently manufacturing alpha on
   exactly the rows where we know least. Such rows are dropped from alpha aggregates instead,
   which is why `n_with_alpha` can be lower than `n_with_return_data`.
2. **Alpha is added ALONGSIDE the absolute fields, never replacing them.** Existing consumers
   read `edge_pct`/`verdict`; silently changing what those mean would be the same class of
   unannounced semantic shift this audit was called in to find.

analytics.py cannot be imported in this test environment (conftest.py stubs `common`
wholesale), so the closures are extracted from source and exec'd with controlled stand-ins for
their free variables — which still exercises the REAL logic rather than asserting on text.
Matches test_bare_gt_zero_hurdle_fix.py's established convention for this file.

See docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md.
"""
import pathlib
import textwrap
from datetime import date

_ANALYTICS_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_SOURCE = _ANALYTICS_PATH.read_text()


def _extract(func_name: str) -> str:
    """Pull one nested def out of filter_audit() and dedent it to module level.

    Stops at the first line that is neither blank nor indented deeper than the `def` itself —
    i.e. at the real end of the function body. Slicing to the NEXT `def` instead would swallow
    whatever sits between the two (here: the price query, which references `rows`).
    """
    lines = _SOURCE.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"    def {func_name}("))
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    body = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= base_indent:
            break
        body.append(ln)
    return textwrap.dedent("\n".join(body))


def _bench_map_from_source() -> dict:
    """Read the REAL benchmark mapping out of analytics.py.

    Originally this test hardcoded {"US": "SPY", "HK": "2800.HK"} into the exec namespace,
    which silently shadowed the source's own value — a sabotage run that pointed HK at SPY
    (the exact bug this change exists to fix) passed all 12 tests. Extracting it from source
    is what makes the per-market assertions real.
    """
    line = next(
        ln for ln in _SOURCE.split("\n") if ln.strip().startswith("_BENCH_SYMBOL_BY_MARKET =")
    )
    ns: dict = {}
    exec(textwrap.dedent(line), ns)  # noqa: S102 — repo-own source
    return ns["_BENCH_SYMBOL_BY_MARKET"]


def _build_bench_return(prices: dict[int, dict[date, float]], bench_ids: dict[str, int]):
    """exec the REAL _bench_symbol_for/_bench_return with a controlled price oracle."""
    ns: dict = {
        "_BENCH_SYMBOL_BY_MARKET": _bench_map_from_source(),
        "_bench_id_by_symbol": bench_ids,
        "date": date,
    }
    exec(_extract("_bench_symbol_for"), ns)  # noqa: S102 — repo-own source, no external input

    def _nearest_price(stock_id, target):
        series = prices.get(stock_id, {})
        future = sorted((d, c) for d, c in series.items() if d >= target)
        return future[0][1] if future else None

    ns["_nearest_price"] = _nearest_price
    exec(_extract("_bench_return"), ns)  # noqa: S102
    return ns["_bench_return"]


_ENTRY, _EXIT = date(2026, 9, 1), date(2026, 9, 9)


# ── Per-market benchmark selection ───────────────────────────────────────────────────────────

def test_hk_is_not_benchmarked_against_spy():
    """Read straight from source. Benchmarking HK against SPY is the precise defect this
    change fixes — it moved measured HK alpha from -6.71% to a flattering -5.56%."""
    m = _bench_map_from_source()
    assert m["HK"] != m["US"], "HK and US must not share a benchmark"
    assert m["HK"] == "2800.HK"
    assert m["US"] == "SPY"


def test_us_and_hk_use_different_benchmarks():
    """The core correctness property: an HK trade must NOT be benchmarked against SPY."""
    prices = {
        1: {_ENTRY: 100.0, _EXIT: 110.0},   # SPY  +10%
        2: {_ENTRY: 100.0, _EXIT: 90.0},    # 2800.HK -10%
    }
    bench = _build_bench_return(prices, {"SPY": 1, "2800.HK": 2})
    assert round(bench("US", _ENTRY, _EXIT), 4) == 0.10
    assert round(bench("HK", _ENTRY, _EXIT), 4) == -0.10


def test_market_may_be_an_enum_like_object_not_only_a_string():
    """Stock.market is a SQLAlchemy Enum in production, a plain string in some call paths —
    _bench_symbol_for must handle both or every real row would silently lose its benchmark."""
    class _Mkt:
        value = "HK"
    prices = {2: {_ENTRY: 100.0, _EXIT: 90.0}}
    bench = _build_bench_return(prices, {"2800.HK": 2})
    assert round(bench(_Mkt(), _ENTRY, _EXIT), 4) == -0.10


def test_unknown_market_yields_none_rather_than_defaulting_to_spy():
    """Silently falling back to SPY for an unrecognised market would reintroduce exactly the
    wrong-benchmark bug this change exists to fix."""
    prices = {1: {_ENTRY: 100.0, _EXIT: 110.0}}
    bench = _build_bench_return(prices, {"SPY": 1})
    assert bench("JP", _ENTRY, _EXIT) is None


# ── The None-not-zero property ───────────────────────────────────────────────────────────────

def test_missing_benchmark_bar_returns_none_not_zero():
    """THE critical property. 0.0 would mean 'flat market', making the raw return read as pure
    alpha — manufacturing alpha on precisely the rows where the benchmark is unknown."""
    bench = _build_bench_return({1: {}}, {"SPY": 1})
    assert bench("US", _ENTRY, _EXIT) is None


def test_benchmark_not_in_stocks_table_returns_none():
    bench = _build_bench_return({}, {})  # benchmark symbols absent entirely
    assert bench("US", _ENTRY, _EXIT) is None


def test_zero_benchmark_entry_price_returns_none_not_a_division_error():
    bench = _build_bench_return({1: {_ENTRY: 0.0, _EXIT: 110.0}}, {"SPY": 1})
    assert bench("US", _ENTRY, _EXIT) is None


# ── Window matching ──────────────────────────────────────────────────────────────────────────

def test_benchmark_is_measured_over_the_same_window_it_is_subtracted_from():
    """A benchmark computed over a different window would silently manufacture or destroy
    alpha. Here the benchmark rises only AFTER the exit date; that move must not be counted."""
    prices = {1: {_ENTRY: 100.0, _EXIT: 100.0, date(2026, 9, 30): 200.0}}
    bench = _build_bench_return(prices, {"SPY": 1})
    assert round(bench("US", _ENTRY, _EXIT), 6) == 0.0


def test_benchmark_uses_the_same_nearest_price_rule_as_the_trade():
    """Both sides resolve through the SAME _nearest_price (first bar on/after target), so a
    non-trading entry date can't apply one rule to the stock and another to the benchmark."""
    prices = {1: {date(2026, 9, 2): 100.0, date(2026, 9, 10): 105.0}}
    bench = _build_bench_return(prices, {"SPY": 1})
    assert round(bench("US", _ENTRY, _EXIT), 4) == 0.05


# ── Wiring: alpha is additive and the sign convention is right ───────────────────────────────

def test_benchmark_ids_are_loaded_through_the_same_price_query_as_traded_symbols():
    """Loading benchmarks via a separate query would risk a different date/timeframe filter."""
    assert "set(_bench_id_by_symbol.values())" in _SOURCE


def test_absolute_fields_are_preserved_alongside_alpha():
    """Non-breaking: existing consumers read these exact keys."""
    for key in ('"edge_pct"', '"verdict"', '"avg_return_active"', '"win_rate_active"'):
        assert key in _SOURCE, f"{key} was removed — this change must be additive"


def test_alpha_verdict_sign_convention_matches_the_absolute_one():
    """Positive edge = the filter suppresses trades that did BETTER = harmful. The alpha twin
    must not invert that, or the two verdicts would contradict each other on the same row."""
    start = _SOURCE.index('"alpha_verdict"')
    body = _SOURCE[start:start + 320]
    assert 'alpha_edge > 0.5' in body and '"harmful"' in body
    assert 'alpha_edge < -0.5' in body and '"predictive"' in body


def test_alpha_edge_is_none_when_either_side_has_no_alpha_rows():
    """Guards a real crash path: subtracting None would raise, and defaulting either side to 0
    would invent an edge from missing data."""
    start = _SOURCE.index("alpha_edge = (")
    body = _SOURCE[start:start + 220]
    assert "is not None and" in body
    assert "else None" in body
