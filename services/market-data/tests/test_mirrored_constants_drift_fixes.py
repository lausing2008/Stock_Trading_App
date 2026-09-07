"""Regression tests for two mirrored-constant divergences found in the 2026-09-06 deep audit
(priority items #17 and #18), both in paper_trading_engine.py.

Root cause common to both: a fallback was written as a re-typed literal (cfg.get(key, <copied
value>)) instead of reading _DEFAULT_CONFIG[key] directly — so when _DEFAULT_CONFIG's own value
changed later, the fallback silently kept the OLD value. The drift-proof pattern
(cfg.get(key, _DEFAULT_CONFIG[key])) is already used correctly elsewhere in this file
(lines ~1938, ~3469, ~3504, and the max_sector_pct ENFORCING gate at ~4433 which uses
cfg["max_sector_pct"] with no default at all) — both fixes below just extend that same pattern
to the two sites that had drifted from it.

Bug (a) — max_signal_age_hours: _DEFAULT_CONFIG's own value was deliberately lowered 96->72
(T222-C, "3 days is sufficient with 5x/day refresh"), but the enforcing signal-staleness gate's
fallback was never updated and stayed at the pre-T222-C literal 96. In the fallback case (any
cfg built without the full _DEFAULT_CONFIG merge), this admitted signals a full day staler than
the documented 72h policy — and disagreed with decision-engine's own hard_rejects.py, which
(already correctly) falls back to 72.

Bug (b) — max_sector_pct: the sector-cap MONITOR's fallback was 0.30 while the real ENFORCING
gate always reads the true 0.25. In the fallback case, sector concentration between 25% and 30%
was hard-blocked at entry but produced NO paper.sector_cap_exceeded warning — an operator
watching the alert stream saw a clean book while entries were silently rejected.
"""
import pathlib

_ENGINE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_ENGINE_SOURCE = _ENGINE_PATH.read_text()


def test_default_config_max_signal_age_hours_is_72():
    """Ground truth: _DEFAULT_CONFIG's own real value (the T222-C fix)."""
    import src.services.paper_trading_engine as pte
    assert pte._DEFAULT_CONFIG["max_signal_age_hours"] == 72


def test_default_config_max_sector_pct_is_025():
    """Ground truth: _DEFAULT_CONFIG's own real value."""
    import src.services.paper_trading_engine as pte
    assert pte._DEFAULT_CONFIG["max_sector_pct"] == 0.25


def test_signal_staleness_gate_fallback_no_longer_uses_the_stale_96_literal():
    """Source-level guard against the exact regression shape: the T195 staleness gate's
    fallback must read _DEFAULT_CONFIG directly, not a re-typed literal that can silently
    drift from it again."""
    anchor = _ENGINE_SOURCE.index("# T195: Signal staleness gate")
    block = _ENGINE_SOURCE[anchor:anchor + 2000]
    assert 'cfg.get("max_signal_age_hours", 96)' not in block, (
        "found the exact pre-fix stale literal — the T222-C change (96->72) never propagated "
        "to this read site, which is the actual ENFORCING gate."
    )
    assert 'cfg.get("max_signal_age_hours", _DEFAULT_CONFIG["max_signal_age_hours"])' in block


def test_sector_cap_monitor_fallback_no_longer_uses_the_diverged_030_literal():
    """Source-level guard against the exact regression shape: the sector-cap monitor's
    fallback must read _DEFAULT_CONFIG directly, matching the enforcing gate elsewhere in this
    same file, instead of the diverged 0.30 literal that silently disagreed with it."""
    anchor = _ENGINE_SOURCE.index("# PA-D1: sector cap monitor")
    block = _ENGINE_SOURCE[anchor:anchor + 1200]
    assert 'cfg.get("max_sector_pct", 0.30)' not in block, (
        "found the exact pre-fix diverged literal — the monitor's fallback (0.30) disagreed "
        "with the real enforcing gate (0.25), so a sector between 25-30% was silently blocked "
        "at entry with no corresponding warning logged."
    )
    assert 'cfg.get("max_sector_pct", _DEFAULT_CONFIG["max_sector_pct"])' in block
