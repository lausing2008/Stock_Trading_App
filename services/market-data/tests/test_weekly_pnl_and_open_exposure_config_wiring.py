"""Tests for T232-DL-DUALSCORER-DEBT — threading two more real, portfolio-wide gates from
paper_trading_engine.py's _scan_for_entries() into decision-engine's config_overrides:

1. Weekly loss/gain circuit breakers (max_weekly_loss_pct / T191's max_weekly_gain_pct) — real
   gates in _scan_for_entries() with zero decision-engine equivalent before this fix. A bad
   week (or a good week worth protecting) was entirely invisible to the live DE-primary path.

2. The open-exposure cap (T194, max_open_exposure_pct) — the one aggregate-exposure sibling of
   the sector-$ cap and open-risk cap (both already ported) that was never ported.

Both are pure portfolio-wide state, computed once per scan cycle (never per-candidate), and
threaded through as a single already-known aggregate rather than a per-candidate projection —
distinct from the sector-$/open-risk caps, which approximate the candidate's own not-yet-sized
contribution using max_position_pct/max_loss_per_trade_pct.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models, which the stubbed conftest.py doesn't provide) — tested via
source-text extraction, matching test_index_trend_config_wiring.py's established technique.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


# ── Weekly loss/gain ───────────────────────────────────────────────────────────

def test_weekly_net_pnl_pct_is_threaded_into_config_overrides():
    assert '"weekly_net_pnl_pct":' in _decision_body
    assert '"max_weekly_loss_pct":' in _decision_body
    assert '"max_weekly_gain_pct":' in _decision_body


def test_weekly_pnl_keys_are_conditional_on_weekly_net_pnl_pct_being_present():
    """All 3 keys must only be sent when a real weekly_net_pnl_pct value is also being
    computed — sending thresholds with no measured value to compare against would be
    meaningless, matching the existing conditional-inclusion pattern used for every other
    gate ported this session. The guard trails AFTER the dict literal in this multi-line
    formatting (unlike index_return_pct's single-line form), so search forward from the
    first key rather than backward from each individual key."""
    start = _decision_body.index('"weekly_net_pnl_pct":')
    end = _decision_body.index("\n", start)
    # Find the closing "if ... else {} )," that terminates this whole conditional block.
    guard_end = _decision_body.index("if weekly_net_pnl_pct is not None else {} )", start)
    assert start < guard_end < guard_end + 2000  # sane upper bound, not unbounded forward scan
    for key in ('"weekly_net_pnl_pct":', '"max_weekly_loss_pct":', '"max_weekly_gain_pct":'):
        key_pos = _decision_body.index(key)
        assert start <= key_pos < guard_end, f"{key} falls outside the conditional block"


def test_weekly_thresholds_fall_back_to_the_real_defaults():
    """The write side's fallback literals must match _scan_for_entries' own real fallbacks
    (cfg.get("max_weekly_loss_pct", 0.08) / cfg.get("max_weekly_gain_pct", 0.06)) exactly —
    not differently-valued literals that would silently diverge from the upstream gate."""
    for key, expected in (
        ('"max_weekly_loss_pct":', 'cfg.get("max_weekly_loss_pct", 0.08)'),
        ('"max_weekly_gain_pct":', 'cfg.get("max_weekly_gain_pct", 0.06)'),
    ):
        start = _decision_body.index(key)
        line_end = _decision_body.index("\n", start)
        line = _decision_body[start:line_end]
        assert expected in line, f"expected {expected!r} on the same line as {key}"


def test_weekly_net_pnl_pct_local_is_hoisted_with_a_typed_none_default():
    """_weekly_net_pnl_pct must be initialized to None BEFORE the conditional block that may
    or may not set it — otherwise later reference to the name (at the _call_decision_engine
    call site) would raise a NameError whenever _needs_weekly is False or equity<=0, since the
    original code only ever defined the local weekly_net_pnl variable INSIDE that block."""
    start = _pte_source.index("_weekly_net_pnl_pct: float | None = None")
    assert start != -1
    if_block_start = _pte_source.index('if _needs_weekly and equity > 0:', start)
    assert start < if_block_start


def test_weekly_net_pnl_pct_is_computed_as_a_signed_percentage_of_equity():
    """Must be weekly_net_pnl / equity * 100 (a signed %, matching the sign convention the
    fallback gate's own weekly_net_pnl < 0 / > 0 branches already use) — not an absolute
    dollar value or an unsigned ratio, either of which would make the read-side's own
    negative-for-loss / positive-for-gain comparison meaningless."""
    assert "_weekly_net_pnl_pct = weekly_net_pnl / equity * 100" in _pte_source


def test_call_site_passes_weekly_net_pnl_pct():
    """The real _call_decision_engine() call site inside _scan_for_entries() must pass through
    the SAME _weekly_net_pnl_pct local computed once earlier in this scan cycle — not a fresh
    per-candidate re-query, matching how every other once-per-cycle aggregate is threaded."""
    assert "weekly_net_pnl_pct=_weekly_net_pnl_pct" in _pte_source


# ── Open-exposure cap ──────────────────────────────────────────────────────────

def test_open_exposure_pct_is_threaded_into_config_overrides():
    assert '"open_exposure_pct":' in _decision_body
    assert '"max_open_exposure_pct":' in _decision_body


def test_open_exposure_pct_falls_back_to_the_real_default_of_40_pct():
    start = _decision_body.index('"max_open_exposure_pct":')
    line_end = _decision_body.index("\n", start)
    line = _decision_body[start:line_end]
    assert 'cfg.get("max_open_exposure_pct", 0.40)' in line


def test_open_exposure_keys_are_conditional_on_open_exposure_pct_being_present():
    """Same multi-line-guard-trails-the-dict structure as the weekly_net_pnl_pct block above
    — search forward for the closing guard rather than backward from each key."""
    start = _decision_body.index('"open_exposure_pct":')
    guard_end = _decision_body.index("if open_exposure_pct is not None else {} )", start)
    assert start < guard_end < guard_end + 1000
    for key in ('"open_exposure_pct":', '"max_open_exposure_pct":'):
        key_pos = _decision_body.index(key)
        assert start <= key_pos < guard_end, f"{key} falls outside the conditional block"


def test_open_exposure_pct_reuses_the_already_summed_sector_values_not_a_second_pass():
    """Must derive from summing _open_sector_values (already computed with the SAME
    _best_price() convention as the sector-$ cap) rather than re-summing
    entry_price*shares over _prefetched_open a SECOND, independent time — a second
    independent sum could silently drift from the sector-cap's own aggregate if either one
    is ever changed without the other."""
    assert "sum(_open_sector_values.values())" in _pte_source


def test_call_site_passes_open_exposure_pct():
    assert "open_exposure_pct=_open_exposure_pct" in _pte_source
