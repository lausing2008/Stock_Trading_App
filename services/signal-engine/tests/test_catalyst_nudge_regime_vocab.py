"""Regression test for AUD-SIGNAL1 (AI Signal deep audit, 2026-09-02) —
AUD-SIGNAL1-STALEREGIMEVOCAB: both catalyst-nudge sites in routes.py whitelisted the OLD
4-state regime vocabulary (bull/high_vol/bear/unknown) years after AUD264-SIGNALENGINE-
SECOND-REGIME-CLASSIFIER migrated market_regime to the canonical 5-state value (bull/neutral/
choppy/risk_off/bear). A real "choppy" or "risk_off" regime (both live-emitted by
/stocks/regime) silently fell through to "unknown" — the LOOSEST threshold tier — reopening
the exact T237-SIG2 failure mode this file's own comment describes, through a different door.

routes.py can't be imported directly in this test environment (its import chain pulls in
fastapi/common.jwt_auth, neither stubbed by conftest.py) — matching this repo's own
established source-text-extraction technique for functions in exactly this situation. These
are regression checks on the WIRING itself: both catalyst-nudge sites must whitelist the full
canonical 5-state vocabulary, not the stale 4-state one.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()

_STALE_VOCAB = '("bull", "high_vol", "bear", "unknown")'
_CANONICAL_VOCAB = '("bull", "neutral", "choppy", "risk_off", "bear", "unknown")'


def test_stale_4state_vocabulary_no_longer_appears_anywhere_in_routes():
    """The old whitelist tuple must not appear anywhere in this file — if it does, at least
    one catalyst-nudge site is still using the pre-AUD264 vocabulary."""
    assert _STALE_VOCAB not in _ROUTES_SOURCE


def test_canonical_5state_vocabulary_appears_at_least_twice():
    """Both catalyst-nudge sites (the scheduled _bulk_persist path and the manual-refresh
    path) must each whitelist the full canonical vocabulary."""
    assert _ROUTES_SOURCE.count(_CANONICAL_VOCAB) >= 2


def test_choppy_and_risk_off_are_real_whitelisted_values():
    assert '"choppy"' in _ROUTES_SOURCE
    assert '"risk_off"' in _ROUTES_SOURCE
    assert '"neutral"' in _ROUTES_SOURCE


def test_regime_whitelist_logic_is_reachable_before_the_dynamic_threshold_lookup():
    """Both sites' regime-normalization line must appear before their own
    _get_dynamic_buy_threshold(...) call — confirms the fix landed in the right place, not
    just somewhere in the file."""
    for marker_var, threshold_call in (("_reg_cat", "_get_bt_cat("), ("_reg_sf", "_get_bt_sf(")):
        normalize_idx = _ROUTES_SOURCE.index(f'{marker_var} = {marker_var} if {marker_var} in {_CANONICAL_VOCAB} else "unknown"')
        threshold_idx = _ROUTES_SOURCE.index(threshold_call, normalize_idx)
        assert normalize_idx < threshold_idx
