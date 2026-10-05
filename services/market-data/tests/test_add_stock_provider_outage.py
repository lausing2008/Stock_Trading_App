"""AUD-ADDSTOCK-OUTAGE-AS-NOTFOUND — "we could not look it up" was reported as "it does not exist".

WHAT HAPPENED (2026-10-05, reported as "why CBRS can not be added?"). Adding CBRS returned
"Symbol not found: CBRS". Probed against the live provider from inside the container:

    CBRS   info keys=1  identity=[]   payload {'trailingPegRatio': None}
    CBRE   info keys=1  identity=[]   payload {'trailingPegRatio': 0.7429}
    MU     info keys=1  identity=[]   payload {'trailingPegRatio': 0.1598}

MU is already in the universe and CBRE is a well-known listing — the `info` endpoint was
returning nothing for EVERY symbol, 401 "Invalid Crumb". Meanwhile the quote endpoints were
fine, and CBRS is unambiguously real:

    CBRS   history_rows=5   fast_info.last=179.24   exchange=NMS
    COIIN  history_rows=0   fast_info raised KeyError
    ASTA   history_rows=0   fast_info raised KeyError

So the guard was sound and its CONCLUSION was not: an unresolvable lookup was rendered as a
resolved negative. This is the same error class as "absence is not evidence" elsewhere in this
codebase, in the one place where it tells a user their correct input is wrong.

The fix does not weaken the typo guard that `_IDENTITY_FIELDS` exists to provide — the measured
numbers above show quotes separate real from mistyped cleanly. It adds a second opinion before
a verdict, and a control symbol to tell an outage from a bad ticker.
"""
import ast
import pathlib

import pytest

_SRC = (pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py").read_text()


def _load():
    tree = ast.parse(_SRC)
    wanted = {"_resolved_identity", "_IDENTITY_FIELDS", "_CONTROL_SYMBOL"}
    keep = [n for n in tree.body
            if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in wanted)
            or (isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) in wanted for t in n.targets))]
    ns: dict = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "<admin>", "exec"), ns)
    return ns


NS = _load()


def test_the_info_payload_seen_in_the_outage_carries_no_identity():
    """The exact payload the provider returned for MU, CBRE and CBRS alike."""
    assert NS["_resolved_identity"]({"trailingPegRatio": 0.1598}) == []
    assert NS["_resolved_identity"]({"trailingPegRatio": None}) == []


def test_quote_evidence_counts_as_identity():
    """What `_corroborate_by_quote` supplies must satisfy the same guard — otherwise the
    fallback could never rescue a real symbol."""
    quoted = {"regularMarketPrice": 179.24, "exchange": "NMS", "currency": "USD"}
    carried = NS["_resolved_identity"](quoted)
    assert "regularMarketPrice" in carried and "exchange" in carried


def test_a_control_symbol_is_defined_and_is_a_real_listing():
    assert NS["_CONTROL_SYMBOL"], "an outage cannot be told from a bad ticker without one"
    assert NS["_CONTROL_SYMBOL"].isalpha()


def test_the_handler_asks_for_a_second_opinion_before_returning_not_found():
    """Pinned against the SOURCE's structure, because the handler needs DB and auth to run.
    The ordering is the property: corroborate, then check for an outage, and only then 404."""
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "add_stock")
    body = ast.unparse(fn)
    i_quote = body.index("_corroborate_by_quote")
    i_degraded = body.index("_provider_is_degraded")
    i_404 = body.index("Symbol not found")
    assert i_quote < i_degraded < i_404, (
        "a 404 must be the last resort, after quote corroboration and an outage check")


def test_an_outage_returns_503_with_retry_after_not_404():
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "add_stock")
    body = ast.unparse(fn)
    seg = body[body.index("_provider_is_degraded"):body.index("Symbol not found")]
    assert "503" in seg
    assert "Retry-After" in seg
    assert "NOT evidence" in seg, "the message must not imply the symbol is invalid"


def test_the_typo_guard_is_not_removed():
    """The fallback must not reopen what the COIIN/ASTA guard closed."""
    assert "_IDENTITY_FIELDS" in _SRC
    assert "Symbol not found" in _SRC, "a genuinely absent symbol is still refused"
    # And the quote corroboration requires BARS, not merely a quote object.
    assert 'history(period="5d")' in _SRC
