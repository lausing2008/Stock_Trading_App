"""AUD-T403-SILENTFEEDOUTAGE — "no expiries" must distinguish a dead feed from a symbol
without options.

THE INCIDENT. Yahoo's options endpoint began returning empty for EVERY symbol (crumb fetch
429'd) around 2026-09-15. All four options endpoints reported `no_options_listed`, and all
three UI panels render nothing on that reason, so the Options tab simply emptied out. The EOD
snapshot job produced zero rows for three consecutive days and nothing said a word. It was
noticed only when a user asked why their page looked different after an unrelated deploy.
"""
import pathlib
import re

_ROUTES = (pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py").read_text()


def test_all_four_options_endpoints_use_the_shared_classifier():
    """Four endpoints hit this same dead end (flow, chain, expirations, game-plan). A fix
    applied to one of them leaves the page three-quarters silent."""
    assert _ROUTES.count("**_empty_chain_reason(session, sym)") == 4


def test_no_endpoint_still_hardcodes_the_misleading_reason():
    """Exactly one literal may remain: the helper's own fallback."""
    assert _ROUTES.count('"reason": "no_options_listed"') == 1


def test_every_call_site_actually_has_a_session_in_scope():
    """Three of these endpoints had no DB session at all. Calling the helper there would have
    raised NameError on the ALREADY-FAILING path — turning a blank panel into a 500."""
    lines = _ROUTES.split("\n")
    call_lines = [i for i, l in enumerate(lines) if "_empty_chain_reason(session, sym)" in l]
    assert len(call_lines) == 4
    for idx in call_lines:
        start = max((i for i in range(idx, -1, -1) if lines[i].startswith("def ")), default=None)
        assert start is not None, "call site is not inside a def"
        body = "\n".join(lines[start:idx + 1])
        assert "session: Session = Depends(get_session)" in body, (
            f"no session injected for the endpoint at line {idx + 1}"
        )


def test_classifier_does_not_make_a_network_call():
    """The failure mode being diagnosed IS rate limiting. Probing the upstream to find out why
    the upstream is rate-limiting is how yfinance-rate-limit-amplification.md begins."""
    start = _ROUTES.index("def _empty_chain_reason(")
    body = _ROUTES[start:_ROUTES.index("\n\n\n", start)]
    for forbidden in ("yf.", "requests.", "httpx", "urlopen", "option_chain("):
        assert forbidden not in body, f"classifier must not call out to {forbidden}"


def test_classifier_evidence_is_a_prior_snapshot_not_a_guess():
    """The distinction rests on a checkable fact — this symbol produced a real chain recently —
    not on a hardcoded list of 'symbols that should have options'."""
    start = _ROUTES.index("def _empty_chain_reason(")
    body = _ROUTES[start:_ROUTES.index("\n\n\n", start)]
    assert "options_game_plan_snapshots" in body
    assert "options_feed_unavailable" in body
    assert "last_known_chain_date" in body


def test_classifier_failure_degrades_to_the_safe_reason():
    """A broken diagnostic must never escalate an unavailable response into a 500."""
    start = _ROUTES.index("def _empty_chain_reason(")
    body = _ROUTES[start:_ROUTES.index("\n\n\n", start)]
    assert "except Exception:" in body
    assert re.search(r"last_seen\s*=\s*None", body), "must fall back to the conservative reason"
