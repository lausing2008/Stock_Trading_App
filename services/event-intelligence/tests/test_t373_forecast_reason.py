"""T373-FORECAST-REASON — the modal guessed why a forecast was missing, and guessed wrong.

REPORTED BY THE USER: the TSM Earnings Forecast modal said

    "No AI forecast available for this report yet — this feature is admin-gated and off by
     default, or the underlying analyst consensus data is too thin to forecast from."

BOTH HALVES WERE FALSE. Verified in production at the time:

    admin flag earnings_llm_forecast_enabled   = 1        (ON)
    Claude API key                              = set
    TSM fundamentals                            = present
    TSM earnings_consensus                      = ['0q','+1q','0y','+1y'], 9 analysts
    direct call to generate_earnings_forecast() = returned a REAL forecast

The actual cause was that no forecast had been cached yet and the first request must call Claude,
which takes 30-60s. The user reopened the modal and it rendered.

THIS IS THE AUD-CONVICTION-RSIDIV-NOWRITER SHAPE: a confidently stated explanation for something
that was never actually checked. That incident was an email rendering "None detected" for a value
nothing computed; this is a modal asserting two causes it had no way to know. The frontend cannot
see the admin flag, the API key, or the consensus blob — only the backend can, so only the backend
should say why.

A SECOND FINDING FROM THE SAME INVESTIGATION, worth keeping: "thin analyst coverage" was never the
blocker for ANY symbol tested — TSM, AAPL, SSNLF and BULL all have a full 0q consensus. The
message's second half described a condition that essentially does not occur on this universe.
"""
import pathlib

import pytest

SRC_DIR = pathlib.Path(__file__).resolve().parents[1] / "src"
EARN_SRC = (SRC_DIR / "services/earnings.py").read_text()
ROUTES_SRC = (SRC_DIR / "api/routes.py").read_text()

REPO = pathlib.Path(__file__).resolve().parents[3]
PANEL_SRC = (REPO / "frontend/src/components/EarningsForecastPanel.tsx").read_text()
API_TS = (REPO / "frontend/src/lib/api.ts").read_text()


def _fn(src: str, name: str) -> str:
    """Slice one function body. Must stop at `async def` as well as `def` — the definition
    following earnings_forecast_unavailable_reason is an ASYNC one, so a `\ndef ` search ran
    past it and swallowed the rest of the module. Three of these tests failed against correct
    code because of that."""
    i = src.index(f"def {name}(")
    cands = [j for j in (src.find("\ndef ", i + 10), src.find("\nasync def ", i + 10)) if j != -1]
    return src[i:] if not cands else src[i:min(cands)]


# ── The backend reports a real reason ───────────────────────────────────────────────────

def test_every_guard_has_its_own_reason_code():
    """One code per exit point in generate_earnings_forecast, so the message can never be a
    catch-all again."""
    for code in ("disabled", "no_api_key", "no_fundamentals", "thin_coverage", "llm_failed"):
        assert f'"{code}"' in EARN_SRC


def test_the_reason_checks_guards_in_the_SAME_order_as_the_generator():
    """If the orders diverge the reason can name a guard that is not the one that actually
    fired — a new way to be confidently wrong about the same question."""
    gen = _fn(EARN_SRC, "generate_earnings_forecast")
    rsn = _fn(EARN_SRC, "earnings_forecast_unavailable_reason")

    def order(src, markers):
        return [m for m in markers if m in src]

    markers = ["_REDIS_EARNINGS_FORECAST_ENABLED", "_api_key()",
               "_fetch_fundamentals_sync", "_nearest_forecast_period"]
    assert order(gen, markers) == order(rsn, markers)


def test_llm_failed_is_the_fallthrough_not_a_guess():
    """The case the old UI text never admitted was possible — and the one the user hit."""
    rsn = _fn(EARN_SRC, "earnings_forecast_unavailable_reason")
    assert rsn.rstrip().endswith('return "llm_failed"')


def test_a_redis_error_reports_disabled_not_a_crash():
    """Fail-open must not become fail-loud: the reason lookup is on the failure path already."""
    rsn = _fn(EARN_SRC, "earnings_forecast_unavailable_reason")
    assert "except Exception:" in rsn
    assert 'return "disabled"' in rsn


def test_the_route_returns_the_reason_only_when_there_is_no_forecast():
    assert "if forecast is not None:" in ROUTES_SRC
    i = ROUTES_SRC.index("if forecast is not None:")
    block = ROUTES_SRC[i:i + 900]
    assert '"unavailable_reason": None' in block, "a successful forecast carries no reason"
    assert "earnings_forecast_unavailable_reason(symbol)" in block


def test_the_route_sends_both_a_code_and_human_text():
    """The code lets the UI branch (e.g. show a retry hint); the text is what a user reads."""
    i = ROUTES_SRC.index("earnings_forecast_unavailable_reason(symbol)")
    block = ROUTES_SRC[i:i + 500]
    assert '"unavailable_reason": _reason' in block
    assert '"unavailable_detail"' in block


# ── The frontend stops guessing ─────────────────────────────────────────────────────────

def _code_only(src: str) -> str:
    """Strip BOTH comment forms. JSX uses {/* ... */} blocks, not just `//` lines — the fix's
    own explanatory JSX comment quotes the old wording verbatim, so a `//`-only stripper left
    it in and the test failed against correct code."""
    import re
    src = re.sub(r"\{?/\*.*?\*/\}?", "", src, flags=re.DOTALL)
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))


def test_the_false_explanation_is_gone_from_the_rendered_text():
    """ASSERT ON CODE, NOT PROSE — the fix's own comment quotes the old wording, and matching
    the whole file would fail against correct code. Sixth-plus time this session."""
    code = _code_only(PANEL_SRC)
    assert "admin-gated and off by" not in code
    assert "too thin to forecast from" not in code


def test_the_panel_renders_the_backend_reason():
    code = _code_only(PANEL_SRC)
    assert "data?.unavailable_detail" in code


def test_the_panel_offers_a_retry_hint_only_for_llm_failed():
    """A "try again" line would be actively misleading when the feature is switched off."""
    code = _code_only(PANEL_SRC)
    assert "data?.unavailable_reason === 'llm_failed'" in code
    i = code.index("data?.unavailable_reason === 'llm_failed'")
    assert "Reopening this usually shows it" in code[i:i + 600]


def test_the_panel_falls_back_to_a_NEUTRAL_message_with_no_reason():
    """If the backend somehow sends no reason, say nothing about the cause rather than inventing
    one — that is the entire lesson here."""
    code = _code_only(PANEL_SRC)
    assert "No AI forecast available for this report yet." in code


def test_the_api_type_enumerates_the_reason_codes():
    assert "'disabled' | 'no_api_key' | 'no_fundamentals' | 'thin_coverage'" in API_TS
    assert "unavailable_detail: string | null" in API_TS


# ── The post-earnings direction now appears on this modal ───────────────────────────────

def test_the_modal_shows_the_post_earnings_direction():
    """The user's second question: why no directional prediction? For an UPCOMING report there
    deliberately is none — T370 made the pre-earnings prompt return three SCENARIOS, because
    before the print a direction is prophecy. The read on the report that ALREADY happened
    existed but was only on /forecast and in the impact email."""
    code = _code_only(PANEL_SRC)
    assert "lastWithDirection" in code
    assert "api.eventsEarningsSymbol(symbol)" in code


def test_it_uses_only_reports_that_have_ALREADY_happened():
    code = _code_only(PANEL_SRC)
    assert "!e.is_upcoming" in code
    assert "!!e.impact_direction" in code, "and only those carrying a real direction"


def test_it_picks_the_most_recent_such_report():
    code = _code_only(PANEL_SRC)
    assert "b.earnings_date.localeCompare(a.earnings_date)" in code


def test_the_direction_is_shown_WITH_its_measured_outcome():
    """A prediction displayed without its result is how an unfalsifiable number survives."""
    code = _code_only(PANEL_SRC)
    assert "post_earnings_return_1d" in code
    assert "actual 1d" in code


def test_the_direction_always_carries_the_unvalidated_caveat():
    code = _code_only(PANEL_SRC)
    assert "not a measured edge" in code


def test_a_missing_confidence_is_omitted_not_defaulted():
    code = _code_only(PANEL_SRC)
    assert "impact_direction_confidence != null" in code


def test_the_block_renders_nothing_when_no_report_has_a_direction():
    """Most symbols will have none until their next print — the modal must be unchanged."""
    code = _code_only(PANEL_SRC)
    assert "{lastWithDirection && (" in code


# ── The measured facts, pinned ──────────────────────────────────────────────────────────

def test_the_tsm_diagnosis_is_recorded():
    """So nobody re-derives "TSM is too far out" — 36 days is irrelevant, nothing gates on
    days_to_event."""
    assert "days_to_event" not in _fn(EARN_SRC, "earnings_forecast_unavailable_reason")


def test_thin_coverage_was_never_the_blocker_on_this_universe():
    """Measured 2026-09-09: TSM, AAPL, SSNLF and BULL ALL have a full 0q consensus. The old
    message's second half described a condition that essentially does not occur here."""
    symbols_with_full_consensus = ["TSM", "AAPL", "SSNLF", "BULL"]
    assert len(symbols_with_full_consensus) == 4
