"""Tests for MPE-05 — threading the Market Pressure Engine's composite squeeze_score
(MPE-01, compute_short_squeeze_score()) into decision-engine's config_overrides via a real,
per-candidate _squeeze_score_for() lookup inside _scan_for_entries().

pressure_score (MPE-02) is deliberately NOT wired into the real call site — its real inputs
(cp_ratio, whale activity) only exist behind a live options-chain yfinance fetch, and this
app's established rate-limit discipline explicitly rules out an options-chain call inside a
hot per-candidate entry-scan loop. The scorer.py Layer 9 itself still supports pressure_score
(tested independently in decision-engine's own test_scorer.py) — it's simply never populated
on the real production path today, a deliberate, disclosed scoping decision, not an oversight.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models) — source-text extraction, matching
test_index_trend_config_wiring.py's established technique exactly.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def _squeeze_score_for_body():
    start = _pte_source.index("def _squeeze_score_for(")
    end = _pte_source.index("\n\ndef _scan_for_entries(", start)
    return _pte_source[start:end]


def test_squeeze_score_is_threaded_into_config_overrides():
    assert '"squeeze_score":' in _decision_body


def test_squeeze_score_is_conditional_on_being_present():
    """Sending squeeze_score=None into config_overrides would be meaningless — must only be
    included when a real value was actually computed, matching every other gate-parity port's
    conditional-inclusion pattern."""
    start = _decision_body.index('"squeeze_score":')
    surrounding = _decision_body[max(0, start - 200):start + 100]
    assert "squeeze_score is not None" in surrounding


def test_pressure_score_parameter_exists_but_is_never_wired_at_the_real_call_site():
    """pressure_score must still be a real, supported parameter (scorer.py's own Layer 9
    reads it) — but the real _scan_for_entries() call site must never pass a value for it,
    a deliberate, disclosed scoping decision (see this file's own module docstring for why)."""
    assert "pressure_score: float | None = None," in _pte_source
    assert "pressure_score=" not in _pte_source.split("def _call_decision_engine(")[1].split("de_result = _call_decision_engine(")[1]


def test_squeeze_score_for_reads_the_already_cached_fundamentals_blob_not_a_fresh_fetch():
    """The whole point of this helper — must never call yfinance directly; it only reads the
    already-cached stockai:fundamentals:v2:{symbol} Redis key routes.py's own short_squeeze()
    endpoint already populates and reads."""
    body = _squeeze_score_for_body()
    assert 'f"stockai:fundamentals:v2:{symbol}"' in body
    assert "import yfinance" not in body


def test_squeeze_score_for_reuses_compute_short_squeeze_score_not_a_reimplementation():
    """Must call the REAL, already-tested compute_short_squeeze_score() function (MPE-01) —
    never a second, independently-reimplemented scoring formula that could silently drift
    from it."""
    body = _squeeze_score_for_body()
    assert "compute_short_squeeze_score(" in body
    assert "from ..api.routes import compute_short_squeeze_score" in body


def test_squeeze_score_for_fails_open_to_none_on_any_exception():
    """Checks the PAIRED handler specifically (except Exception: immediately followed by
    return None) — a bare 'both substrings appear somewhere in the function' check would be
    trivially satisfied by the two unrelated early-return guards elsewhere in this same
    function, silently missing a sabotaged handler that re-raises instead of failing open."""
    body = _squeeze_score_for_body()
    assert "except Exception:\n        return None" in body


def test_squeeze_score_for_returns_none_when_no_fundamentals_cache_entry_exists():
    """A symbol with no cached fundamentals blob must degrade to None, not raise — the
    fundamentals-refresh job doesn't cover every symbol at every moment."""
    body = _squeeze_score_for_body()
    assert "if not cached:" in body
    assert "return None" in body


def test_squeeze_score_for_returns_none_when_short_percent_of_float_is_missing():
    """Matches compute_short_squeeze_score()'s own contract — short_percent_of_float is the
    one load-bearing input; a candidate with none at all should never fabricate a score."""
    body = _squeeze_score_for_body()
    assert "if spf is None:" in body


def test_call_site_passes_a_fresh_per_candidate_lookup_not_a_once_per_cycle_value():
    """Unlike index_return_pct (computed once per scan cycle), squeeze_score genuinely varies
    per candidate (each symbol's own short-interest/momentum) — must be a fresh
    _squeeze_score_for() call per candidate, not a single value reused across the whole loop."""
    assert "_squeeze_score_val = _squeeze_score_for(" in _pte_source
    assert "squeeze_score=_squeeze_score_val" in _pte_source


def test_momentum_score_input_reuses_the_already_loaded_ranking_object():
    """The momentum_score argument must come from the SAME `ranking` object kscore_f already
    reads from a few lines earlier — not a second, independent Ranking query."""
    start = _pte_source.index("_squeeze_score_val = _squeeze_score_for(")
    end = _pte_source.index("\n", start + 200)
    call_site = _pte_source[start:end]
    assert "ranking.momentum" in call_site
