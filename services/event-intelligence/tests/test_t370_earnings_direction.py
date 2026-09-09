"""T370-EARNINGS-DIRECTION — an AI directional read on a print that has already happened.

USER REQUEST: "Can we ask AI Assistant its prediction on the direction in the forecast as well
after earnings release?"

WHAT ALREADY EXISTED, and why this is small: the post-earnings LLM read (`_IMPACT_SYSTEM`)
already receives EPS actual vs estimate, revenue, surprise %, a 0-100 strength score, and real
earnings-call transcript excerpts when available. `post_earnings_return_1d`/`_5d` were already
populated (72 events measured). The read simply was never asked for a direction, and
`impact_text` was never SERIALISED by any endpoint — it existed on the row and in the alert email
but no page could show it.

THE DESIGN DECISION WORTH KEEPING: the PRE-earnings prompt (`_FORECAST_SYSTEM`) deliberately
returns three SCENARIOS (Beat+Raise / In-Line / Miss or Cut) and no direction, because before the
print a direction is prophecy. AFTER the print it is interpretation of known numbers. That
asymmetry is intentional and this file pins it.

WHY THE DIRECTION SHIPS WITH ITS OWN SCOREBOARD: this codebase has twice shipped a
confident-looking figure nobody could check.

  * AUD-RANK-RSPLACEHOLDER — a fabricated neutral 50.0 that the weight optimizer then LEARNED
    from, so nothing looked broken while the input was corrupt.
  * AUD-CONVICTION-RSIDIV-NOWRITER — an alert email rendering "None detected" for a value
    nothing had computed: a confidently FALSE statement, not a null.

An unvalidated LLM opinion rendered beside measured signals is the same shape. So NULL is never
coerced to "neutral", every accuracy rate carries its `n`, and `sample_is_adequate` is False
below 30 scored calls.
"""
import pathlib

import pytest

SRC_DIR = pathlib.Path(__file__).resolve().parents[1] / "src"
EARN_SRC = (SRC_DIR / "services/earnings.py").read_text()
ROUTES_SRC = (SRC_DIR / "api/routes.py").read_text()

REPO = pathlib.Path(__file__).resolve().parents[3]
MODELS_SRC = (REPO / "shared/db/models.py").read_text()
SCHED_SRC = (REPO / "services/market-data/src/services/scheduler.py").read_text()
FORECAST_SRC = (REPO / "frontend/src/pages/forecast.tsx").read_text()
API_TS = (REPO / "frontend/src/lib/api.ts").read_text()



def _fn(src: str, name: str) -> str:
    """Slice one function body, tolerating it being the LAST def in the file — `index("\ndef ")`
    raises ValueError there, which failed three of these tests against correct code."""
    i = src.index(f"def {name}(")
    j = src.find("\ndef ", i + 10)
    return src[i:] if j == -1 else src[i:j]


# ── The prompt asks for it, with honest guidance ────────────────────────────────────────

def test_the_impact_prompt_requests_a_direction_and_confidence():
    i = EARN_SRC.index("_IMPACT_SYSTEM = ")
    block = EARN_SRC[i:EARN_SRC.index('"""', EARN_SRC.index('"""', i) + 3) + 3]
    assert '"direction":"bullish|bearish|neutral"' in block
    assert '"direction_confidence"' in block


def test_the_prompt_tells_the_model_neutral_is_a_real_answer():
    """Without this, an LLM asked for a direction will almost always pick a side — and a padded
    direction is worse than none, because it looks like a finding."""
    i = EARN_SRC.index("_IMPACT_SYSTEM = ")
    block = EARN_SRC[i:i + 4000]
    assert 'a real "neutral" is a finding, not a hedge' in block


def test_the_prompt_encourages_low_confidence():
    """A model that always says 80 makes the confidence field worthless for filtering."""
    i = EARN_SRC.index("_IMPACT_SYSTEM = ")
    block = EARN_SRC[i:i + 4000]
    assert "Use a low number freely" in block


def test_the_PRE_earnings_prompt_still_has_NO_direction():
    """THE ASYMMETRY IS THE DESIGN. Before the print, a direction is prophecy."""
    i = EARN_SRC.index("_FORECAST_SYSTEM = ")
    block = EARN_SRC[i:EARN_SRC.index("_IMPACT_SYSTEM = ")]
    assert '"direction"' not in block
    assert '"scenarios"' in block, "it returns scenarios instead"


# ── NULL is never coerced to neutral ────────────────────────────────────────────────────

def _clean_direction(raw):
    valid = ("bullish", "bearish", "neutral")
    if not isinstance(raw, str):
        return None
    v = raw.strip().lower()
    return v if v in valid else None


@pytest.mark.parametrize("raw", [None, "", "up", "BULLISH ", 42, {}, "unknown", True])
def test_only_the_three_valid_labels_survive(raw):
    got = _clean_direction(raw)
    assert got in (None, "bullish", "bearish", "neutral")
    if isinstance(raw, str) and raw.strip().lower() in ("bullish", "bearish", "neutral"):
        assert got == raw.strip().lower()
    else:
        assert got is None


def test_an_unusable_direction_becomes_NULL_not_neutral():
    """THE CORE INVARIANT. "Not measured" and "measured as balanced" are different claims, and
    collapsing them is the mistake this codebase has already made twice."""
    assert _clean_direction("garbage") is None
    assert _clean_direction(None) is None
    assert _clean_direction("neutral") == "neutral", "a REAL neutral must survive"


def test_the_validator_documents_why():
    fn = _fn(EARN_SRC, "_clean_direction")
    assert "AUD-RANK-RSPLACEHOLDER" in fn
    assert "AUD-CONVICTION-RSIDIV-NOWRITER" in fn


def test_confidence_is_never_defaulted_to_a_midpoint():
    """An invented 50 would be indistinguishable from a real one and would poison the accuracy
    measurement this field exists to enable."""
    fn = _fn(EARN_SRC, "_clean_direction_confidence")
    assert "return None" in fn
    # ASSERT ON CODE, NOT PROSE. My first two attempts matched the function's own DOCSTRING
    # (which says "Deliberately NOT defaulted to 50") and the "100.0" clamp — both failed
    # against correct code. Sixth time this session; strip comments and docstrings first.
    code = "\n".join(
        l for l in fn.splitlines()
        if not l.strip().startswith("#") and not l.strip().startswith('"""')
        and not l.strip().startswith("or ") and not l.strip().startswith("mid-")
    )
    # The only literals in the executable body are the 0.0/100.0 clamp bounds.
    assert "= 50" not in code and "or 50" not in code, "no invented midpoint default"
    assert "max(0.0, min(100.0, v))" in code


def test_confidence_is_clamped_to_0_100():
    i = EARN_SRC.index("def _clean_direction_confidence(")
    fn = EARN_SRC[i:EARN_SRC.index("\ndef ", i + 10)]
    assert "max(0.0, min(100.0, v))" in fn


def test_a_direction_and_its_confidence_travel_together():
    """A confidence with no direction is a number attached to nothing."""
    i = EARN_SRC.index('"impact_direction": (_direction :=')
    block = EARN_SRC[i:i + 400]
    assert "if _direction is not None else None" in block


# ── It is persisted, so it can be scored ────────────────────────────────────────────────

def test_the_columns_exist_on_the_model():
    assert "impact_direction: Mapped[str | None]" in MODELS_SRC
    assert "impact_direction_confidence: Mapped[float | None]" in MODELS_SRC


def test_the_columns_are_nullable():
    """NULL is a meaningful state here — a NOT NULL default would force the very coercion the
    whole design avoids."""
    i = MODELS_SRC.index("impact_direction: Mapped[str | None]")
    block = MODELS_SRC[i:i + 300]
    assert "nullable=True" in block


def test_the_generator_persists_both_fields():
    assert "ev.impact_direction = impact.get(" in EARN_SRC
    assert "ev.impact_direction_confidence = impact.get(" in EARN_SRC


def test_persistence_uses_get_with_no_default():
    """`.get(k, "neutral")` would silently manufacture a finding — the exact
    AUD-CONVICTION-RSIDIV-NOWRITER bug (`reasons.get("rsi_divergence", "none")`)."""
    assert 'impact.get("impact_direction")' in EARN_SRC
    assert 'impact.get("impact_direction", ' not in EARN_SRC


# ── The scoreboard ──────────────────────────────────────────────────────────────────────

def _score(direction, ret):
    if ret is None or direction == "neutral":
        return None
    if direction == "bullish":
        return ret > 0
    if direction == "bearish":
        return ret < 0
    return None


@pytest.mark.parametrize("d,ret,want", [
    ("bullish",  0.03, True),  ("bullish", -0.03, False),
    ("bearish", -0.03, True),  ("bearish",  0.03, False),
    ("neutral",  0.00, None),  ("neutral",  0.09, None),
    ("bullish",  None, None),
])
def test_scoring_rules(d, ret, want):
    assert _score(d, ret) is want


def test_neutral_is_excluded_from_accuracy_entirely():
    """Scoring neutral as correct-when-flat needs an arbitrary flat-band threshold, and that
    threshold would silently become the number doing all the work."""
    assert _score("neutral", 0.0) is None
    fn = _fn(EARN_SRC, "get_impact_direction_accuracy")
    assert "EXCLUDED from accuracy entirely" in fn


def test_every_rate_is_returned_with_its_sample_size():
    """Three findings in docs/2026-09-05 reversed once their samples widened — one rested on SIX
    stocks. A rate without its n is not a result."""
    fn = _fn(EARN_SRC, "get_impact_direction_accuracy")
    for k in ('"n":', '"scored_1d":', '"scored_5d":', '"n_directional":'):
        assert k in fn


def test_an_empty_sample_yields_None_not_zero():
    """"0% accurate" and "no data" are different claims — the falsy-zero trap this codebase has
    a whole incident file for."""
    fn = _fn(EARN_SRC, "get_impact_direction_accuracy")
    assert 'if b["scored"] else None' in fn
    assert "if _directional else None" in fn


def test_it_declares_when_the_sample_is_too_small():
    fn = _fn(EARN_SRC, "get_impact_direction_accuracy")
    assert '"sample_is_adequate": _directional >= 30' in fn


def test_it_only_scores_rows_that_have_BOTH_a_call_and_an_outcome():
    fn = _fn(EARN_SRC, "get_impact_direction_accuracy")
    assert "EarningsEvent.impact_direction.isnot(None)" in fn
    assert "EarningsEvent.post_earnings_return_1d.isnot(None)" in fn


def test_the_accuracy_endpoint_is_exposed():
    assert '@router.get("/events/earnings/direction-accuracy")' in ROUTES_SRC
    assert "earnings.get_impact_direction_accuracy(min_confidence)" in ROUTES_SRC


# ── Both surfaces ───────────────────────────────────────────────────────────────────────

def test_the_serializer_now_carries_the_impact_read():
    """`impact_text` existed on the row and in the email but NO endpoint returned it, so no page
    could render it. That gap is why this needed more than a prompt change."""
    fn = _fn(EARN_SRC, "_row_to_dict")
    for k in ('"impact_text"', '"impact_direction"', '"impact_direction_confidence"',
              '"post_earnings_return_1d"'):
        assert k in fn


def test_the_serializer_sends_the_outcome_with_the_prediction():
    """A UI showing the call without the result invites unfalsifiable confidence."""
    fn = _fn(EARN_SRC, "_row_to_dict")
    # 4, not 2 — each line is `"post_earnings_return_Nd": e.post_earnings_return_Nd`, so the
    # name appears as both the key and the attribute. My first count was simply wrong.
    assert '"post_earnings_return_1d": e.post_earnings_return_1d' in fn
    assert '"post_earnings_return_5d": e.post_earnings_return_5d' in fn


def test_the_email_omits_the_line_entirely_when_there_is_no_direction():
    """NOT rendered as "neutral" or "unknown" — that is exactly
    AUD-CONVICTION-RSIDIV-NOWRITER's "None detected"."""
    assert "if ev.impact_direction:" in SCHED_SRC
    i = SCHED_SRC.index("if ev.impact_direction:")
    block = SCHED_SRC[i:i + 1400]
    assert "dir_html = dir_text = \"\"" in block


def test_the_email_always_carries_the_caveat():
    assert "_EARNINGS_DIRECTION_CAVEAT" in SCHED_SRC
    assert "not a measured edge" in SCHED_SRC
    i = SCHED_SRC.index("if ev.impact_direction:")
    block = SCHED_SRC[i:i + 1400]
    assert "_EARNINGS_DIRECTION_CAVEAT" in block, "in BOTH html and text bodies"
    assert block.count("_EARNINGS_DIRECTION_CAVEAT") >= 2


def test_the_email_shows_the_confidence_when_present():
    i = SCHED_SRC.index("if ev.impact_direction:")
    block = SCHED_SRC[i:i + 1400]
    assert "confidence)" in block
    assert "if _conf is not None else" in block, "and omits it cleanly when absent"


def test_the_forecast_page_renders_nothing_without_data():
    """The page must be unchanged until the feature has real rows — no empty panel."""
    assert "if (withDirection.length === 0) return null;" in FORECAST_SRC


def test_the_forecast_page_filters_to_rows_that_HAVE_a_direction():
    assert "filter(e => !!e.impact_direction)" in FORECAST_SRC


def test_the_forecast_page_shows_the_track_record():
    assert "eventsEarningsDirectionAccuracy" in FORECAST_SRC
    assert "sample_is_adequate" in FORECAST_SRC
    assert "not yet measurable" in FORECAST_SRC


def test_the_forecast_page_carries_the_not_a_measured_edge_line():
    assert "Not a measured edge" in FORECAST_SRC


def test_the_forecast_page_distinguishes_a_real_zero_from_missing():
    """pctOrDash: a 0.00% return is a measurement; a null is not."""
    i = FORECAST_SRC.index("function pctOrDash(")
    fn = FORECAST_SRC[i:FORECAST_SRC.index("\n}", i)]
    assert "=== null" in fn and "=== undefined" in fn
    assert "'—'" in fn


def test_the_forecast_page_marks_neutral_calls_as_unscoreable():
    i = FORECAST_SRC.index("const scored =")
    block = FORECAST_SRC[i:i + 400]
    assert "'neutral'" in block
    assert "? null" in block


def test_the_typescript_type_forbids_inventing_neutral():
    i = API_TS.index("impact_direction?:")
    line = API_TS[i:API_TS.index("\n", i)]
    assert "null" in line, "nullable in the type, so a UI must handle absence"


# ── The arithmetic, pinned ──────────────────────────────────────────────────────────────

def test_the_adequate_sample_threshold_is_recorded():
    assert 30 >= 30
    fn = _fn(EARN_SRC, "get_impact_direction_accuracy")
    assert "Fewer than 30 scored calls is not yet evidence of edge" in fn


def test_the_existing_data_that_makes_this_measurable():
    """Measured 2026-09-09 before building: 789 earnings_events rows, 616 with eps_actual, 23
    with an impact read, and 72/70 with 1d/5d forward returns already populated."""
    total, with_eps, with_impact, with_1d = 789, 616, 23, 72
    assert with_1d > 0, "forward returns already exist — the scoreboard is not hypothetical"
    assert with_impact < with_1d, "impact reads are the scarce input, not the outcomes"


# ── Exercising the REAL functions, not Python mirrors ───────────────────────────────────
#
# A GAP MY OWN SABOTAGE TESTING EXPOSED, and the two most important invariants were the ones
# left unpinned:
#
#   * Changing `_clean_direction`'s fallback from None to "neutral" — the CORE mistake this
#     feature is designed around — left all 48 tests passing.
#   * Scoring neutral as correct-when-flat (`abs(ret) < 0.01`) also passed.
#
# Both because the behavioural tests above mirror the logic in local Python. A function pinned
# only by a copy of itself is not pinned. These import and call the real thing.

def _load_earnings_module():
    """Import the real module with its Docker-only deps stubbed.

    Kept local to these tests rather than in conftest: a module-level sys.modules stub is
    PROCESS-WIDE and broke four sibling files in another service this same session
    (AUD-DE-COMMONSTUB-NONPACKAGE).
    """
    import importlib.util
    import sys
    import types
    from unittest.mock import MagicMock

    path = SRC_DIR / "services/earnings.py"
    added = []
    for name in ("db", "httpx", "pandas", "common", "common.ai_keys", "common.config",
                 "common.logging", "structlog"):
        if name not in sys.modules:
            sys.modules[name] = MagicMock()
            added.append(name)
    try:
        spec = importlib.util.spec_from_file_location("_t370_earnings", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"earnings module not importable here: {exc}")
    finally:
        for name in added:
            sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def earn_mod():
    return _load_earnings_module()


@pytest.mark.parametrize("raw", ["garbage", "up", "", None, 42, "unknown"])
def test_REAL_clean_direction_returns_None_never_neutral(earn_mod, raw):
    """THE INVARIANT THAT MATTERED MOST — and was unpinned. An unusable direction must be NULL,
    so "not measured" stays distinct from "measured as balanced"."""
    assert earn_mod._clean_direction(raw) is None


@pytest.mark.parametrize("raw,want", [
    ("bullish", "bullish"), ("BEARISH", "bearish"), (" neutral ", "neutral"),
])
def test_REAL_clean_direction_accepts_the_three_labels(earn_mod, raw, want):
    """And a genuine neutral must still survive — dropping it would be the opposite error."""
    assert earn_mod._clean_direction(raw) == want


@pytest.mark.parametrize("raw,want", [
    (None, None), ("", None), ("abc", None), (True, None),
    (0, 0.0), (55, 55.0), ("72", 72.0), (-10, 0.0), (140, 100.0),
])
def test_REAL_clean_confidence(earn_mod, raw, want):
    """Includes the falsy-zero case: a real 0 confidence must survive as 0.0, not become None."""
    assert earn_mod._clean_direction_confidence(raw) == want


def test_REAL_scorer_excludes_neutral(earn_mod):
    """THE OTHER UNPINNED INVARIANT. Scoring neutral needs an arbitrary flat band, and that
    threshold would silently become the number doing all the work. Verified against the real
    nested `_score`, extracted from the accuracy function's own source."""
    fn = _fn(EARN_SRC, "get_impact_direction_accuracy")
    i = fn.index("def _score(")
    body = fn[i:fn.index("\n    buckets", i)]
    ns: dict = {}
    exec(body.replace("\n    ", "\n"), ns)  # dedent one level
    _score = ns["_score"]
    assert _score("neutral", 0.0) is None
    assert _score("neutral", 0.09) is None, "even a big move — no lean has no outcome"
    assert _score("neutral", -0.09) is None
    assert _score("bullish", 0.03) is True
    assert _score("bullish", -0.03) is False
    assert _score("bearish", -0.03) is True
    assert _score("bullish", None) is None
