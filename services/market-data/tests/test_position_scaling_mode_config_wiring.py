"""Tests for T241-POSITION-SCALING-DESIGN — position_scaling_mode had the EXACT same
T232-CONFIGGAP gap as llm_scoring_enabled/risk_check_enabled before them: the entire
shadow-mode mechanism (log real verdicts against real trades before ever considering going
live) was fully built and code-complete in paper_trading_engine.py, but position_scaling_mode
was never in paper_portfolio.py's /configure allowed_keys set — meaning there was literally
no way for anyone to turn shadow mode on for a real portfolio through the app. Confirmed live
in production: every real portfolio had this stuck at "off" (or unset), and both
ps:shadow:pending/ps:shadow:resolved Redis lists were completely empty as a direct result.

Unlike llm_scoring_enabled/risk_check_enabled (which get threaded into decision-engine's
config_overrides), position_scaling_mode is read directly from cfg inside
_scan_for_entries() itself — no decision-engine wiring needed, only the allowed_keys entry
and enum validation (only "off"/"shadow" are real, implemented values; "live" order
placement doesn't exist anywhere in the engine yet, so accepting that string would silently
no-op rather than doing anything).

paper_portfolio.py can't be imported directly in this test environment — tested via
source-text extraction, matching test_risk_check_config_wiring.py's established technique
exactly.
"""
import pathlib

_pp_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_pp_source = _pp_path.read_text()


def _load_configure_portfolio_body():
    start = _pp_source.index("def configure_portfolio(")
    end = _pp_source.index("\n\n\n@router.post", start)
    return _pp_source[start:end]


_configure_body = _load_configure_portfolio_body()


def test_position_scaling_mode_is_in_allowed_keys():
    """The exact regression this guards against: setting position_scaling_mode via
    POST /configure would previously be silently dropped as an unrecognized key.

    Scoped specifically to the allowed_keys set (not the whole function body) — a bare
    substring search across the entire function would ALSO match the unrelated
    _ENUM_CHECKS dict key a few lines below, silently passing even if the real
    allowed_keys entry were removed."""
    allowed_keys_start = _configure_body.index("allowed_keys = {")
    allowed_keys_end = _configure_body.index("\n    }", allowed_keys_start)
    allowed_keys_section = _configure_body[allowed_keys_start:allowed_keys_end]
    assert '"position_scaling_mode"' in allowed_keys_section


def test_position_scaling_mode_has_enum_validation():
    """Only "off"/"shadow" are real, implemented values — accepting an arbitrary string
    (especially "live", which sounds plausible but doesn't exist in the engine at all) would
    silently save a value the engine never checks for, rather than erroring."""
    assert '"position_scaling_mode"' in _configure_body
    enum_start = _configure_body.index("_ENUM_CHECKS")
    enum_section = _configure_body[enum_start:enum_start + 600]
    assert '"off", "shadow"' in enum_section


def test_live_is_explicitly_rejected_not_silently_accepted():
    """Confirms the enum set does NOT include "live" — this must be a validation error, not
    a value that gets silently saved and then does nothing because the engine only checks
    for the literal string "shadow"."""
    enum_start = _configure_body.index("_ENUM_CHECKS")
    enum_section = _configure_body[enum_start:enum_start + 600]
    # The allowed-values set itself must be exactly {"off", "shadow"} — "live" must not
    # appear anywhere inside this specific validation block.
    values_start = enum_section.index("{")
    values_end = enum_section.index("}", values_start)
    assert "live" not in enum_section[values_start:values_end]


def test_enum_check_runs_before_the_generic_allowed_keys_filter():
    """Validation must happen before the value is ever written to the DB — confirms the
    _ENUM_CHECKS loop iteration happens before the `updated = {...}` dict comprehension that
    actually persists the config."""
    enum_check_idx = _configure_body.index("if key in _ENUM_CHECKS")
    updated_idx = _configure_body.index("updated = {k: v for k, v in body.items()")
    assert enum_check_idx < updated_idx
