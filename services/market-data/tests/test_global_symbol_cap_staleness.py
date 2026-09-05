"""AUD-GLOBALSYMCAP-STALE: the cross-portfolio per-symbol cap went stale mid-scan.

T221-A added `max_positions_per_symbol_global` (default 1) to stop the same symbol being held
in more than one portfolio at once. The counter backing it, `_global_sym_open`, is loaded ONCE
from the DB before the candidate loop — and was never incremented as positions actually opened
during that same run. So the moment a scan opened its first position in a symbol, the guard's
answer for every later candidate of that symbol went stale and still read 0.

The per-portfolio guard directly above it (`open_symbols.add(stock.symbol)`) DOES update on
entry. That asymmetry was the bug: one guard stayed live, its cross-portfolio sibling did not.

Confirmed in production: 2382.HK opened THREE positions on 2026-06-25, all stopped out the same
day, two of them in the SAME portfolio — ~HKD 72k of combined exposure to a single idea, losing
$5,143. That one symbol accounts for 64% of the entire paper-trading net loss to date
(-$8,029), and the three trades were not three decisions: they were one idea sized 3x by a
counter that never noticed the first two.

paper_trading_engine.py can't be imported here (heavy dependency chain), so this verifies the
real source text plus a behavioral model of the counter, matching this repo's established
technique.
"""
import pathlib

_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py").read_text()


def _scan_fn() -> str:
    start = _SOURCE.index("_max_global_per_sym = cfg.get(")
    return _SOURCE[start:start + 40000]


# ── the counter is kept live ─────────────────────────────────────────────────

def test_global_counter_is_incremented_on_entry():
    assert "_global_sym_open[stock.symbol] = _global_sym_open.get(stock.symbol, 0) + 1" in _SOURCE


def test_increment_sits_beside_the_per_portfolio_guard():
    """The two guards must stay in lockstep — that they diverged is what caused the bug."""
    src = _SOURCE
    add_idx = src.index("open_symbols.add(stock.symbol)")
    inc_idx = src.index("_global_sym_open[stock.symbol] = _global_sym_open.get(stock.symbol, 0) + 1")
    assert 0 < inc_idx - add_idx < 1200, "increment should immediately follow open_symbols.add()"


def test_increment_happens_after_the_trade_is_confirmed():
    """Must not count a candidate that failed to open (trade is None -> continue), or the cap
    would block real entries on a symbol nothing was ever bought in."""
    src = _SOURCE
    guard_idx = src.index("if trade is None:")
    inc_idx = src.index("_global_sym_open[stock.symbol] = _global_sym_open.get(stock.symbol, 0) + 1")
    assert guard_idx < inc_idx


def test_counter_is_still_seeded_from_the_database():
    """The in-loop increment supplements the initial DB load; it must not replace it, or
    positions opened by a PREVIOUS run would stop being counted."""
    fn = _scan_fn()
    assert "PaperTrade.stage == \"open\"" in fn
    assert "_global_sym_open[_gsym] = _gcnt" in fn


def test_cap_check_still_reads_the_counter():
    fn = _scan_fn()
    assert "_global_sym_open.get(stock.symbol, 0) >= _max_global_per_sym" in fn


def test_default_cap_is_one_position_per_symbol_globally():
    assert '"max_positions_per_symbol_global": 1,' in _SOURCE


# ── behavior of the guard with a live counter ────────────────────────────────

def _admits(counter: dict, symbol: str, cap: int, already_in_this_portfolio: bool) -> bool:
    """The exact condition the scan applies."""
    if not already_in_this_portfolio and counter.get(symbol, 0) >= cap:
        return False
    return True


def test_second_candidate_for_same_symbol_is_now_blocked():
    """The 2382.HK case: first entry opens, second must be refused."""
    counter: dict = {}
    assert _admits(counter, "2382.HK", 1, False) is True
    counter["2382.HK"] = counter.get("2382.HK", 0) + 1     # first entry opens
    assert _admits(counter, "2382.HK", 1, False) is False  # second is refused


def test_third_candidate_also_blocked():
    counter = {"2382.HK": 2}
    assert _admits(counter, "2382.HK", 1, False) is False


def test_stale_counter_would_have_admitted_all_three():
    """Documents the pre-fix behavior this regression guards against — without the increment,
    the counter stays at 0 and every candidate is admitted."""
    counter: dict = {}
    admitted = sum(1 for _ in range(3) if _admits(counter, "2382.HK", 1, False))
    assert admitted == 3, "pre-fix: a never-incremented counter admits every candidate"


def test_unrelated_symbols_are_unaffected():
    counter = {"2382.HK": 1}
    assert _admits(counter, "AAPL", 1, False) is True


def test_symbol_already_held_in_this_portfolio_bypasses_the_cap():
    """That path routes to scale-in, which is deliberate existing behavior — the cap is about
    opening a NEW position elsewhere, not about adding to one already held here."""
    counter = {"AAPL": 1}
    assert _admits(counter, "AAPL", 1, True) is True


def test_cap_of_zero_disables_the_guard_entirely():
    """cfg allows 0 = feature off; the seeding block is itself gated on `> 0`."""
    fn = _scan_fn()
    assert "if _max_global_per_sym > 0 and candidate_syms:" in fn
