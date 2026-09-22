"""AUD-NEWSGATE-UNMEASURED (2026-09-22) — hot_news_flag is now scored by filter_audit().

`hot_news_flag` was the ONE compressing gate in this engine that no measurement path ever
scored: absent from both `SUPPRESSION_NAMED` and `SUPPRESSION_BOOLEAN` in filter_audit(), and
with no outcome table of its own — unlike squeeze / prebreakout / options-flow / dark-pool,
which all have one in shared/db/models.py.

That blind spot is how a BACKWARDS sign survived undetected. Measured over 150 days of
Alpaca-sourced news on tracked symbols, benchmark-relative against SPY over each event's own
matched window, the ordering is negative > neutral > positive on BOTH mean and median, in BOTH
the material and non-material groups:

    material negative  n=977   alpha +1.64%     <- the HIGHEST bucket
    material neutral   n=274   alpha +1.29%
    material positive  n=1321  alpha +0.77%

...yet "material_negative" is precisely the value that compresses the fused score by x0.70
(first hour) / x0.85 (second hour) at generators/signals.py:2466-2470. The gate suppresses BUY
signals on exactly the population with the best subsequent returns.

This file does not fix the sign — flipping it is a live-behaviour change that must be measured,
not assumed, and measuring it is what these tests enable. See
docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md for the full evidence.

analytics.py is a large FastAPI route module with heavy DB/session dependencies and cannot be
imported directly in this test environment (conftest.py stubs the `common` package wholesale),
so the dict is extracted from source and exec'd into a controlled namespace — which still gives
REAL predicate behaviour to assert on, rather than text matching alone. Matches
test_bare_gt_zero_hurdle_fix.py's established source-extraction convention for this same file.
"""
import pathlib
import re

_ANALYTICS_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_ANALYTICS_SOURCE = _ANALYTICS_PATH.read_text()

_SIGNALS_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "generators" / "signals.py"
)
_SIGNALS_SOURCE = _SIGNALS_PATH.read_text()


def _suppression_named() -> dict:
    """Extract and exec the real SUPPRESSION_NAMED dict so the predicates can be CALLED."""
    start = _ANALYTICS_SOURCE.index("SUPPRESSION_NAMED = {")
    end = _ANALYTICS_SOURCE.index("\n    }", start) + len("\n    }")
    block = _ANALYTICS_SOURCE[start:end]
    # Strip the leading indentation so the assignment is valid at module level.
    block = "\n".join(line[4:] if line.startswith("    ") else line for line in block.split("\n"))
    ns: dict = {}
    exec(block, ns)  # noqa: S102 — controlled, repo-own source, no external input
    return ns["SUPPRESSION_NAMED"]


# ── The gate is now measured at all ──────────────────────────────────────────────────────────

def test_hot_news_flag_is_present_in_suppression_named():
    """The regression this file exists to prevent — the gate silently unscored again."""
    assert "hot_news_flag" in _suppression_named()


def test_hot_news_flag_reaches_the_scored_filter_name_list():
    """Presence in the dict is only useful if filter_audit actually iterates it into
    all_filter_names — otherwise the gate would be 'registered' but still never reported."""
    assert "list(SUPPRESSION_NAMED.keys())" in _ANALYTICS_SOURCE


# ── The predicate matches the RIGHT value ────────────────────────────────────────────────────

def test_predicate_fires_on_material_negative():
    assert _suppression_named()["hot_news_flag"]("material_negative") is True


def test_predicate_does_not_fire_on_material_other():
    """'material_other' is logged-but-never-applied (signals.py) — counting it as a suppression
    would dilute the very effect this measurement exists to expose."""
    assert _suppression_named()["hot_news_flag"]("material_other") is False


def test_predicate_does_not_fire_on_none_or_missing():
    pred = _suppression_named()["hot_news_flag"]
    assert pred("none") is False
    assert pred(None) is False


# ── Cross-file consistency: the measured value must be the value that actually compresses ────

def test_every_hot_news_flag_value_written_by_signals_py_is_known_here():
    """Cross-file guard: if signals.py ever introduces a NEW hot_news_flag value, this test
    fails so the measurement predicate gets revisited deliberately rather than silently
    continuing to score only the old vocabulary."""
    written = set(re.findall(r'reasons\["hot_news_flag"\]\s*=\s*"([a-z_]+)"', _SIGNALS_SOURCE))
    assert written == {"material_negative", "material_other", "none"}, (
        f"signals.py hot_news_flag vocabulary changed: {sorted(written)} — "
        "revisit the SUPPRESSION_NAMED predicate in analytics.py"
    )


def test_the_measured_value_is_the_one_that_actually_compresses_the_score():
    """The whole point: the value scored as a suppression must be the value that really reduces
    `fused`. If these ever diverge, filter_audit would be scoring a flag that does nothing."""
    start = _SIGNALS_SOURCE.index('hot_news = base_reasons.get("hot_news")')
    body = _SIGNALS_SOURCE[start:start + 2000]
    # The compressing branch is keyed on a negative sentiment label and writes material_negative.
    assert 'sentiment_label") == "negative"' in body
    assert 'reasons["hot_news_flag"] = "material_negative"' in body
    assert _suppression_named()["hot_news_flag"]("material_negative") is True
