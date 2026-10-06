"""Budget logic that needs no store. The ENFORCEMENT tests live in the integration suite.

WHY THE SPLIT. Earlier versions of this file drove threads against a fake Redis and a fake
session and reported that concurrency and atomicity were covered. They were not: running the
same properties against a real PostgreSQL immediately found an INSERT branch with no ceiling
check and two counters spending the same ceiling. A double that mirrors the intended behaviour
proves the mirror.

So what remains here is the pure logic — the scope hierarchy, the admission estimate, the day
key, the settlement semantics — and everything that depends on a store is asserted against a
real one in `test_llm_budget_integration.py`.
"""
import inspect
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import llm_budget as B  # noqa: E402


# ============================================================ the admission estimate

def test_the_estimate_includes_the_configured_maximum_output():
    assert B.upper_bound_tokens("x", 1600) - B.upper_bound_tokens("x", 0) == 1600


@pytest.mark.parametrize("label,text", [
    ("english prose", "Micron Technology reports record fourth quarter results"),
    ("tickers and numbers", "MU NVDA $54.23B +12.18% Q4 FY2026 non-GAAP EPS 33.42"),
    ("json formatting", '[{"i": 0, "headline": "x"}, {"i": 1, "headline": "y"}]'),
    ("cjk", "美光科技公布創紀錄的第四季度和全年業績，營收達到五百四十二億美元"),
    ("emoji", "📈📉🚀" * 20),
    ("combining marks", "é" * 50 + "ā̈ŏ̃" * 20),
    ("mathematical alphanumerics", "𝕄𝕚𝕔𝕣𝕠𝕟 𝐓𝐞𝐜𝐡𝐧𝐨𝐥𝐨𝐠𝐲" * 5),
    ("base64 blob", "aGVsbG8gd29ybGQ=" * 20),
])
def test_the_estimate_never_falls_below_the_utf8_byte_floor(label, text):
    """Bytes, not characters: measured on these exact strings, CJK is 3.0 bytes per character
    and emoji 4.0, so the character-based estimate this replaced under-reserved precisely
    where it mattered."""
    assert B.upper_bound_tokens(text, 0) >= len(text.encode("utf-8")), label


def test_the_estimate_allows_for_serialisation_and_message_framing():
    """Byte length bounds the CONTENT. The provider accounts for the serialised request plus
    per-message framing, which byte length alone does not cover."""
    assert B.upper_bound_tokens("x" * 1000, 0) > 1000
    assert B._FRAMING_TOKENS_PER_MESSAGE > 0
    assert B._SERIALISATION_FACTOR > 1.0


def test_it_is_described_as_admission_control_not_a_proven_ceiling():
    """An earlier version claimed an unconditional hard ceiling from JSON byte length and was
    not entitled to: the provider's tokenizer is not this code."""
    doc = inspect.getdoc(B.upper_bound_tokens)
    assert "ADMISSION CONTROL" in doc
    flat = " ".join(doc.split())      # the phrase wraps across lines in the docstring
    assert "not a proven provider-side ceiling" in flat
    assert "WHAT IT DOES NOT ESTABLISH" in doc


# ============================================================ scopes and days

def test_a_nested_scope_resolves_to_itself_and_every_ceiling_above_it():
    assert B.scope_chain(B.SCOPE_RESOLVER_FALLBACK) == [
        B.SCOPE_RESOLVER_FALLBACK, B.SCOPE_NEWS_CLASSIFY]
    assert B.scope_chain(B.SCOPE_NEWS_CLASSIFY) == [B.SCOPE_NEWS_CLASSIFY]


def test_the_fallback_allowance_is_smaller_than_the_ceiling_it_sits_under(monkeypatch):
    monkeypatch.delenv("LLM_BUDGET_NEWS_CLASSIFY", raising=False)
    monkeypatch.delenv("LLM_BUDGET_NEWS_CLASSIFY_RESOLVER_FALLBACK", raising=False)
    assert 0 < B.budget_for(B.SCOPE_RESOLVER_FALLBACK) < B.budget_for(B.SCOPE_NEWS_CLASSIFY)


def test_the_day_is_the_utc_calendar_day_and_says_so():
    k = B.utc_day_key(B.SCOPE_NEWS_CLASSIFY,
                      datetime(2026, 10, 6, 23, 30, tzinfo=timezone.utc))
    assert k.endswith("2026-10-06")


# ============================================================ fail closed

def test_an_unavailable_ledger_defers_rather_than_admitting(monkeypatch):
    """There is deliberately no second counter to fall back to: a fallback is a second
    authority, and two authorities cannot bound one ceiling."""
    monkeypatch.setattr(B, "_db_reserve",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger down")))
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    r = B.reserve("x", 0)
    assert not r.allowed and r.enforcement == "none"
    assert "cannot be reserved or recorded" in r.reason


def test_a_zero_budget_means_no_ceiling_not_no_capacity(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "0")
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY_RESOLVER_FALLBACK", "0")
    r = B.reserve("x" * 10_000_000, 0)
    assert r.allowed and "no ceiling configured" in r.reason


def test_redis_is_not_consulted_for_the_admission_decision():
    """Redis holds an advisory copy for display. It decides nothing — which is what removed
    the question of what is true between two writes to two stores."""
    import ast
    src = (Path(__file__).resolve().parents[1] / "common" / "llm_budget.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "reserve")
    body = ast.unparse(fn)
    assert "_redis" not in body, "the admission decision must not depend on a second store"
    assert "_db_reserve" in body
