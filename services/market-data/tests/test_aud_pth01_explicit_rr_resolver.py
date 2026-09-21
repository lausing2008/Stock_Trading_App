"""Tests for AUD-PTH01-EXPLICITRESOLVER (PT-H01, docs/audits/2026-09-19-paper-trading-horizon-
threshold-audit.md), independently re-derived and confirmed in
docs/audits/2026-09-19-paper-trading-horizon-audit-review.md.

Root cause: `cfg.get("min_rr_ratio", _default_min_rr_ratio(...))`'s fallback NEVER actually
fired, because `_DEFAULT_CONFIG["min_rr_ratio"] = 2.0` is a hardcoded literal every portfolio's
resolved config carries explicitly (confirmed live: all 11 active portfolios store
min_rr_ratio=2.0) — `dict.get()`'s default only triggers when the key is ABSENT, never when it
merely equals what the calibrated value would produce. So AUD-MINRR-MARKETBLIND/
AUD-MINRR-STYLEBLIND's own calibration never actually reached a live entry decision through the
base floor, only through `regime_min_rr_ratio` (genuinely absent from `_DEFAULT_CONFIG`).

The fix (`resolve_min_rr_ratio()`/`resolve_regime_min_rr_ratio()`) makes the ambiguity explicit
via a new `min_rr_ratio_mode` config key instead of changing what every live portfolio resolves
to: "manual" (default, i.e. every existing portfolio) is byte-identical to today's actual
behavior; "calibrated" is a new, explicit, opt-in mode that always uses the calibrated value.

Pure functions with one external dependency (_load_min_rr_override(), file/cache-backed) —
extracted via exec() (matching test_min_rr_calibration_by_style.py's established technique)
with that one dependency stubbed to a controlled in-memory dict, so calibration routing can be
tested precisely without touching the real override file or module-level cache.
"""
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_SOURCE = _PATH.read_text()


def _extract_resolver_block() -> str:
    """_default_min_rr_ratio() through resolve_regime_min_rr_ratio() — one contiguous block."""
    start = _SOURCE.index("def _default_min_rr_ratio(")
    end = _SOURCE.index("\n\n\n# ── Default portfolio config", start)
    return _SOURCE[start:end]


def _load(override: dict):
    namespace = {"_load_min_rr_override": lambda: override}
    exec(_extract_resolver_block(), namespace)  # noqa: S102 — isolated eval of real source
    return namespace["resolve_min_rr_ratio"], namespace["resolve_regime_min_rr_ratio"]


# ── "manual" mode (default — every existing portfolio) must be byte-identical to today ────────

def test_manual_mode_is_the_default_when_min_rr_ratio_mode_is_unset():
    resolve_min_rr, _ = _load({})
    # Every real portfolio's resolved config stores min_rr_ratio explicitly (PT-H01's own
    # finding) — the stored value must win, exactly as it always has.
    assert resolve_min_rr({"min_rr_ratio": 2.0, "market": "US", "trading_style": "SWING"}) == 2.0


def test_manual_mode_with_a_genuinely_custom_stored_value_is_respected():
    resolve_min_rr, _ = _load({})
    assert resolve_min_rr({"min_rr_ratio": 2.5, "market": "US", "trading_style": "SWING"}) == 2.5


def test_manual_mode_falls_through_to_calibrated_default_only_when_key_is_truly_absent():
    """The one case where the old cfg.get(...) fallback actually fired — a portfolio config
    with NO min_rr_ratio key at all. Must still work identically under the new resolver."""
    resolve_min_rr, _ = _load({"min_rr_ratio": 2.4})
    assert resolve_min_rr({"market": "US", "trading_style": "SWING"}) == 2.4


def test_manual_mode_regime_floor_falls_through_when_absent_using_real_regime_state():
    """regime_min_rr_ratio genuinely is absent from _DEFAULT_CONFIG — this is the one path
    that DID already reach calibration before this fix, and must keep doing so."""
    _, resolve_regime = _load({"regime_min_rr_ratio": 3.4})
    assert resolve_regime({"market": "US", "trading_style": "SWING"}, "choppy") == 3.4


def test_manual_mode_with_no_calibration_ever_run_falls_back_to_the_original_literals():
    resolve_min_rr, resolve_regime = _load({})
    assert resolve_min_rr({"market": "US", "trading_style": "SWING"}) == 2.0
    assert resolve_regime({"market": "US", "trading_style": "SWING"}, "risk_off") == 3.0


# ── "calibrated" mode — new, explicit, opt-in; ignores any stored numeric value outright ──────

def test_calibrated_mode_ignores_the_stored_value_and_uses_the_pooled_calibration():
    resolve_min_rr, _ = _load({"min_rr_ratio": 2.25})
    cfg = {"min_rr_ratio": 2.0, "min_rr_ratio_mode": "calibrated", "market": "US", "trading_style": "SWING"}
    assert resolve_min_rr(cfg) == 2.25


def test_calibrated_mode_prefers_by_style_over_by_market_over_pooled():
    """Same precedence _default_min_rr_ratio() already documents — this resolver must not
    reimplement or shortcut that precedence, just route to it unconditionally in this mode."""
    override = {
        "min_rr_ratio": 2.25,
        "by_market": {"US": {"min_rr_ratio": 2.10}},
        "by_style": {"SWING": {"min_rr_ratio": 2.0}},
    }
    resolve_min_rr, _ = _load(override)
    cfg = {"min_rr_ratio": 9.9, "min_rr_ratio_mode": "calibrated", "market": "US", "trading_style": "SWING"}
    assert resolve_min_rr(cfg) == 2.0


def test_calibrated_mode_also_applies_to_the_regime_tiered_floor():
    resolve_min_rr, resolve_regime = _load({"regime_min_rr_ratio": 3.5})
    cfg = {"regime_min_rr_ratio": 9.9, "min_rr_ratio_mode": "calibrated", "market": "US", "trading_style": "SWING"}
    assert resolve_regime(cfg, "choppy") == 3.5


def test_min_rr_ratio_mode_is_case_insensitive():
    resolve_min_rr, _ = _load({"min_rr_ratio": 2.25})
    cfg = {"min_rr_ratio": 2.0, "min_rr_ratio_mode": "CALIBRATED", "market": "US", "trading_style": "SWING"}
    assert resolve_min_rr(cfg) == 2.25


# ── No live portfolio's resolved value changes — the whole point of the fix ───────────────────

def test_the_exact_pt_h01_scenario_all_11_live_portfolios_resolve_unchanged():
    """PT-H01's own confirmed live finding: every active portfolio stores min_rr_ratio=2.0
    explicitly, with no min_rr_ratio_mode key (since it didn't exist before this fix). Their
    resolved value must be exactly 2.0 after this change, not the calibrated pooled value —
    this is the guarantee that makes the fix safe to deploy with no validation framework yet."""
    resolve_min_rr, _ = _load({"min_rr_ratio": 2.25})  # a real, live calibration value on disk
    cfg = {"min_rr_ratio": 2.0, "market": "US", "trading_style": "SWING"}  # real stored shape, no mode key
    assert resolve_min_rr(cfg) == 2.0, "a live portfolio's floor must not silently move to the calibrated value"


# ── Call-site wiring — both real production call sites must route through these resolvers ─────

def test_should_enter_routes_through_both_resolvers():
    start = _SOURCE.index("def _should_enter(")
    end = _SOURCE.index("\ndef ", start + 1)
    body = _SOURCE[start:end]
    assert "min_rr = resolve_min_rr_ratio(cfg)" in body
    assert "resolve_regime_min_rr_ratio(cfg, regime_state)" in body
    assert 'cfg.get("min_rr_ratio", _default_min_rr_ratio(' not in body, (
        "must not have left a second, un-migrated inline resolution"
    )


def test_call_decision_engine_routes_through_both_resolvers():
    start = _SOURCE.index("de_url = _gs_de().decision_engine_url")
    end = _SOURCE.index("\n        if r.status_code", start)
    body = _SOURCE[start:end]
    assert '"min_rr_ratio":           resolve_min_rr_ratio(cfg),' in body
    assert '"regime_min_rr_ratio":    resolve_regime_min_rr_ratio(cfg, regime_state),' in body
