"""Tests for AUD262-BREAKEVEN-COOLDOWN-60X-TOO-SHORT (Deep Audit #2, Tier 262).

_monitor_positions()'s breakeven_stop classification previously compared only the STOP LEVEL
to entry (abs(stop - entry) <= entry*0.005) — completely independent of the actual FILL
(live_price). A gap-down or next-cycle price can fill well BELOW a breakeven-tolerance stop,
realizing a real loss while still being labeled "breakeven_stop" (which gets only a 2-hour
re-entry cooldown vs stop_hit's 120 hours). Production evidence: 22 of 26 breakeven_stop
trades LOST money, worst -5.18%.

Fix: breakeven_stop now additionally requires the FILL itself to be within tolerance of entry
(abs(live_price - entry) <= entry*0.005). A fill that gapped meaningfully below the stop falls
through to stop_hit instead. The sibling trailing_stop branch (stop ratcheted above entry) was
ALSO tightened during this fix — `stop > entry` alone was insufficient, since a stop only
marginally above entry (failing the breakeven branch's own live_price check) combined with a
hard gap-down fill well below entry would otherwise still satisfy `stop > entry` and be
mislabeled a profitable "trailing_stop" exit. Added `and live_price >= entry`.

_monitor_positions() can't be exercised end-to-end in this test environment (heavy DB/session/
live-price dependencies) — the exit-classification branch is a small, pure decision (given
entry/stop/live_price, which of 4 labels applies) extracted directly from the real source and
exec()'d, matching test_trailing_stop_label_split.py's/test_min_ta_score_config_wiring.py's
established source-text-extraction technique for this exact import-constraint class.
"""
import pathlib

_engine_path = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_engine_source = _engine_path.read_text()


def _build_classify_stop_breach():
    """Extracts the REAL if/elif/elif/else classification chain (from `_be_tol = ...` through
    the stop_hit assignment) verbatim out of the source and wraps it in a function, so this
    test exercises the actual code under test — not a hand-copied reimplementation that could
    silently drift from it. The block is indented 12 spaces in situ (nested under the outer
    `elif live_price <= stop:` inside `_monitor_positions()`); textwrap.dedent brings it down
    to a plain 0-indent chain that a fresh `def` body (indented 4 spaces) can safely wrap."""
    import textwrap

    start = _engine_source.index("            _be_tol = entry * 0.005")
    marker = 'exit_reason = "stop_hit"'
    end = _engine_source.index(marker, start) + len(marker)
    block = textwrap.dedent(_engine_source[start:end])
    indented = "\n".join(("    " + ln if ln.strip() else ln) for ln in block.splitlines())
    func_source = "def classify(entry, stop, live_price):\n" + indented + "\n    return exit_reason\n"
    # _base_notes/pnl_pct are referenced by the real branch bodies' exit_notes dict
    # construction (a side effect this test doesn't care about, but must not crash on) —
    # supplied as harmless stand-ins so the REAL exit_reason assignment logic still runs
    # unmodified.
    namespace: dict = {"_base_notes": {}, "pnl_pct": 0.0}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of the real classification logic
    return namespace["classify"]


_classify_stop_breach = _build_classify_stop_breach()


def test_genuine_breakeven_exit_stop_near_entry_fill_near_entry():
    assert _classify_stop_breach(entry=100.0, stop=100.2, live_price=100.1) == "breakeven_stop"


def test_genuine_trailing_stop_exit_stop_ratcheted_up_fill_near_stop():
    assert _classify_stop_breach(entry=100.0, stop=113.0, live_price=113.89) == "trailing_stop"


def test_genuine_protective_loss_cut_stop_below_entry_fill_near_stop():
    assert _classify_stop_breach(entry=100.0, stop=88.0, live_price=87.5) == "stop_hit"


def test_gap_through_a_breakeven_tolerance_stop_is_a_real_loss_not_breakeven():
    """The exact production failure scenario this fix closes: a stop within breakeven
    tolerance of entry, but the fill gapped hard below both stop and entry — a real loss,
    must NOT be labeled breakeven_stop."""
    result = _classify_stop_breach(entry=100.0, stop=100.3, live_price=85.0)
    assert result == "stop_hit"
    assert result != "breakeven_stop"


def test_gap_through_a_stop_marginally_above_entry_is_a_real_loss_not_trailing_stop():
    """The edge case this fix's OWN second guard (`and live_price >= entry`) closes: a stop
    only marginally above entry (just outside the breakeven branch's own live_price check)
    combined with a hard gap-down fill well below entry must not satisfy `stop > entry` alone
    and be mislabeled a profitable trailing_stop exit."""
    result = _classify_stop_breach(entry=100.0, stop=100.3, live_price=85.0)
    assert result != "trailing_stop"
    assert result == "stop_hit"


def test_small_gap_still_within_tolerance_of_entry_counts_as_breakeven():
    """A small dip just below entry, still within the 0.5% tolerance on both stop and fill,
    is genuinely a near-flat exit — must still classify as breakeven_stop, not stop_hit."""
    assert _classify_stop_breach(entry=100.0, stop=100.3, live_price=99.8) == "breakeven_stop"
