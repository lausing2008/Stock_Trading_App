"""Tests for AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK's calibration-persistence fix inside
_bulk_persist() (routes.py).

Background: _calibrated_win_rate() has always computed a real, measured historical win rate
per confidence band/horizon/direction/market — but it was only ever written into `ai.reasons`
on TWO manual-refresh HTTP response paths, both of which enrich AFTER their own DB commit
already ran. _scan_for_entries() (paper_trading_engine.py, the real trading engine) only ever
reads `Signal.reasons` from the DB — so the value was computed, displayed to users on the
stock page, and calculated correctly, but never once reached anything a live entry decision
could see.

_bulk_persist() is the ONLY function that durably persists Signal.reasons (it runs on the
real 5x/day schedule and its upsert is the one write path _scan_for_entries() actually reads
from). This fix adds the SAME calibration lookup, fetched once per symbol (matching the
existing T220-G sector-rotation fetch's own established shape), and writes it into `ai.reasons`
BEFORE the per-style upsert serializes and persists that dict.

routes.py can't be imported directly in this environment (conftest.py stubs `common`/`db`
wholesale, and this module does `from common.jwt_auth import get_current_username` at import
time) — so this file does source-text regression checks against the real, current source,
matching the established technique this repo already uses for exactly this class of
Docker-only-dependency constraint (see test_backfill_realized_ev.py's own docstring for the
same reasoning, applied to a different file in the same repo).
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _bulk_persist_body() -> str:
    start = _ROUTES_SOURCE.index("def _bulk_persist(")
    # _bulk_persist is the last top-level function in the file before any @router decorator
    # that follows it — bound the extraction at the first @router.get/@router.post after the
    # function's own start, matching test_backfill_realized_ev.py's own end-marker convention.
    next_router = _ROUTES_SOURCE.index("\n@router.", start)
    return _ROUTES_SOURCE[start:next_router]


def test_bulk_persist_function_exists_and_is_extractable():
    body = _bulk_persist_body()
    assert "def _bulk_persist(symbols: list[str]) -> None:" in body
    assert len(body) > 500


def test_calibration_map_is_fetched_once_per_symbol_not_once_per_style():
    """The fetch (_get_confidence_calibration(s)) must sit OUTSIDE the loop that actually
    consumes it (the one containing the F2 confidence_delta annotation and the upsert) — inside
    it would mean one redundant Redis/DB round-trip per style (4x) instead of once per symbol,
    the exact inefficiency the sibling T220-G sector-rotation fetch was already careful to
    avoid. NOTE: `for style_key, ai in all_sig.items():` also appears earlier in this same
    function (the unrelated 40-B cross-horizon-consensus block) — anchor on the SPECIFIC loop
    that contains the upsert, not the first occurrence of that loop header string."""
    body = _bulk_persist_body()
    fetch_idx = body.index("_cal_map_bp = _get_confidence_calibration(s)")
    upsert_idx = body.index("rsns=json.dumps(_json_safe(ai.reasons))")
    # the loop header immediately preceding the fetch is the one this fix actually cares about
    loop_idx = body.rindex('for style_key, ai in all_sig.items():', 0, upsert_idx)
    assert fetch_idx < loop_idx, (
        "the calibration map must be fetched BEFORE the per-style loop starts, not once per "
        "style inside it"
    )


def test_enrichment_only_applies_to_buy_or_sell_signals():
    """A HOLD/WAIT signal has no real 'did this call turn out to be right' outcome distribution
    to calibrate against (SignalOutcome.is_correct is only ever computed for BUY/SELL rows) —
    enriching a HOLD/WAIT signal's reasons with a calibrated_win_rate would be meaningless, or
    worse, silently misleading if a future reader assumed the field's presence implies a real
    directional call."""
    body = _bulk_persist_body()
    assert 'if ai.signal in ("BUY", "SELL") and _cal_map_bp:' in body


def test_enrichment_writes_both_win_rate_and_sample_count():
    """A bare win_rate with no sample-count context is exactly the kind of unqualified figure
    T232-OC5 raised the confidence-calibration minimum-sample floor to guard against —
    persisting the count alongside the rate lets any future consumer judge its own trust in
    the number, matching the live-response paths' own established field-pair convention."""
    body = _bulk_persist_body()
    assert 'ai.reasons["calibrated_win_rate"] = _cwr_bp[0]' in body
    assert 'ai.reasons["calibrated_win_rate_count"] = _cwr_bp[1]' in body


def test_enrichment_guards_against_a_none_reasons_dict():
    """ai.reasons can legitimately be None at this point (e.g. a signal whose reasons were
    never populated for some other reason) — writing into it without first initializing an
    empty dict would raise a real TypeError, aborting the whole symbol's persist loop."""
    body = _bulk_persist_body()
    enrich_start = body.index('if ai.signal in ("BUY", "SELL") and _cal_map_bp:')
    enrich_end = body.index("# F2: annotate confidence_delta before upsert")
    enrich_block = body[enrich_start:enrich_end]
    assert "if ai.reasons is None:" in enrich_block
    assert "ai.reasons = {}" in enrich_block


def test_enrichment_only_writes_when_calibrated_win_rate_returns_a_real_value():
    """_calibrated_win_rate() returns None when no bucket has enough samples (below
    _CONF_CAL_MIN_COUNT) or horizon/direction weren't supplied — the enrichment must skip
    writing anything in that case rather than persisting a fabricated 0.0/None pair that a
    future reader could mistake for 'measured, zero win rate' instead of 'unmeasurable'."""
    body = _bulk_persist_body()
    enrich_start = body.index('if ai.signal in ("BUY", "SELL") and _cal_map_bp:')
    enrich_end = body.index("# F2: annotate confidence_delta before upsert")
    enrich_block = body[enrich_start:enrich_end]
    assert "if _cwr_bp is not None:" in enrich_block


def test_enrichment_happens_strictly_before_the_upsert_that_persists_reasons():
    """The whole point of this fix: the enrichment must land in ai.reasons BEFORE the
    INSERT ... ON CONFLICT ... DO UPDATE upsert serializes reasons via json.dumps(_json_safe(
    ai.reasons)) — landing it after would repeat the exact bug this fix closes (a value
    computed but never actually persisted anywhere durable)."""
    body = _bulk_persist_body()
    enrich_idx = body.index('ai.reasons["calibrated_win_rate"] = _cwr_bp[0]')
    upsert_idx = body.index("rsns=json.dumps(_json_safe(ai.reasons))")
    assert enrich_idx < upsert_idx


def test_calibration_fetch_uses_the_same_session_the_upsert_itself_uses():
    """_get_confidence_calibration(s) must be called with the SAME `s` SessionLocal() context
    the rest of the function's DB work uses — a different/new session here would be a wasted
    connection and a needless divergence from every other per-symbol DB read in this block."""
    body = _bulk_persist_body()
    assert "_cal_map_bp = _get_confidence_calibration(s)" in body


def test_market_is_resolved_defensively_for_both_enum_and_plain_string_market_values():
    """Stock.market may be a real Market enum instance (the common case) or, in some code
    paths elsewhere in this app, a plain already-unwrapped string — _calibrated_win_rate()
    needs a plain string either way to build its lookup key. A naive `.value` access on an
    already-plain-string market would raise AttributeError."""
    body = _bulk_persist_body()
    assert (
        '_stock_mkt_bp = stock.market.value if hasattr(stock.market, "value") else stock.market'
        in body
    )
