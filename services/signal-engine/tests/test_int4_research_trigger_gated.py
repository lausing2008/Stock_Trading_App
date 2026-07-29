"""Regression tests for CLAUDE-API-COST-AUDIT (2026-07-29 follow-up).

_bulk_persist()'s INT-4 auto-trigger-research-on-BUY-signal call site
(`_httpx.post(f"{_url}/research/{symbol}/trigger", ...)`) was NEVER gated by
`auto_research_enabled` — the 2026-07-28 cost audit only found and fixed market-data's own
scheduler-side `_auto_trigger_research()` (a bounded top-5-per-cycle sweep). This completely
independent call site fires on EVERY symbol with a BUY signal on ANY horizon, every
_bulk_persist() cycle, with no cap at all. Live production evidence: 46 distinct symbols had a
BUY signal in one 24h window, and research-engine logged 68 real Sonnet report generations the
same day despite the user never clicking "Generate Report."

_bulk_persist() itself is not exercised end-to-end here (250+ lines, heavy DB/HTTP
dependencies disproportionate to this fix's actual scope) — matching this repo's established
precedent for functions of this shape (e.g. _monitor_positions()'s own test file). These are
source-text regression checks on the specific new gate.
"""
import pathlib

_ROUTES_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
)
_SOURCE = _ROUTES_PATH.read_text()


def _int4_block() -> str:
    """The INT-4/INT-7 block inside _bulk_persist()'s per-symbol loop — from the BUY/STRONG
    BUY check through the end of its except clause."""
    start = _SOURCE.index('if ai.signal in ("BUY", "STRONG BUY"):')
    end = _SOURCE.index('log.debug("divergence_check.failed"', start)
    end = _SOURCE.index("\n", end) + 1
    return _SOURCE[start:end]


def test_trigger_post_is_gated_by_the_auto_research_enabled_flag():
    """The exact fix: the /trigger POST must be wrapped in a check against the same Redis
    key market-data's _auto_trigger_research() already gates on."""
    block = _int4_block()
    assert 'stockai:admin:feature:auto_research_enabled' in block
    gate_idx = block.index('if _get_redis().get("stockai:admin:feature:auto_research_enabled") == "1"')
    trigger_idx = block.index('_httpx.post(f"{_url}/research/{symbol}/trigger"')
    assert gate_idx < trigger_idx, "the flag check must wrap the /trigger POST, not follow it"


def test_gate_uses_exact_string_equality_to_enabled_not_a_falsy_check():
    """Must require an explicit "1" (matching _auto_trigger_research()'s own convention) —
    not a bare truthiness check that would treat an unset/None Redis value as enabled."""
    block = _int4_block()
    assert '== "1"' in block


def test_summary_get_and_int7_divergence_check_are_not_gated():
    """INT-7's research-divergence logging reads the SAME /summary response this fetch
    already makes — a real, useful, already-cached-data-only read that costs nothing. The
    fix must gate ONLY the /trigger POST (which schedules a new Sonnet generation), never
    the /summary GET or the divergence check that follows it."""
    block = _int4_block()
    summary_get_idx = block.index('_httpx.get(\n                                    f"{_url}/research/{symbol}/summary"')
    gate_idx = block.index('if _get_redis().get("stockai:admin:feature:auto_research_enabled") == "1"')
    trigger_idx = block.index('_httpx.post(f"{_url}/research/{symbol}/trigger"')
    # the summary GET must be OUTSIDE the gate's own if-body (i.e. after the gated
    # trigger call's block ends, at the same indentation as _research_fetched's own
    # assignment) — confirmed by checking it's not between gate_idx and trigger_idx+POST-line
    assert summary_get_idx > trigger_idx
    divergence_idx = block.index('if _rec in ("AVOID", "SELL")')
    assert divergence_idx > summary_get_idx


def test_trigger_call_stays_inside_the_pre_existing_try_except_fail_open_block():
    """A Redis outage on this new gate check must never crash signal persistence for the
    whole symbol — it must be caught by the SAME outer except Exception this block already
    has (log.debug("divergence_check.failed"), never raised further)."""
    full_block_start = _SOURCE.index('if ai.signal in ("BUY", "STRONG BUY"):')
    try_idx = _SOURCE.index("try:", full_block_start)
    gate_idx = _SOURCE.index('_get_redis().get("stockai:admin:feature:auto_research_enabled")', full_block_start)
    except_idx = _SOURCE.index("except Exception as _rdiv_exc:", full_block_start)
    assert try_idx < gate_idx < except_idx


def test_research_fetched_is_still_set_true_regardless_of_the_gate_result():
    """_research_fetched must still flip to True whether or not the trigger actually fired —
    otherwise a disabled flag would cause this block to re-attempt the (gated, no-op) trigger
    AND re-fetch /summary on every single BUY-style iteration for the same symbol within one
    _bulk_persist() call, instead of once."""
    block = _int4_block()
    fetched_true_idx = block.rindex("_research_fetched = True")
    gate_idx = block.index('if _get_redis().get("stockai:admin:feature:auto_research_enabled") == "1"')
    assert gate_idx < fetched_true_idx
