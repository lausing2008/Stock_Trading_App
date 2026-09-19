"""AUD-E08-FRESHNESSFAILOPEN — check_signal_alerts()'s DP-3 price-freshness gate collapsed two
distinct failure modes into the same fallback: "the freshness query found zero price rows for
any alert symbol" (missing data — a genuine cold-start/empty-DB case, safe to fail open on) and
"the query found rows, but EVERY one of them is stale" (a real, current data problem — NOT safe
to fail open on) both hit the exact same `not fresh_symbols and symbols` check and got the exact
same treatment: every symbol waved through as if it were fresh. The log line even read "No price
bars found for any alert symbol," which was false in the second case.

Fixed by distinguishing `price_rows is None` (the query itself never ran — a DB error) or an
empty result (`price_rows == []`) from a non-empty result where every row was too old to count
as fresh. Only the first two cases still fail open; the third now logs at ERROR and leaves
fresh_symbols empty, which the existing `if alert.symbol not in fresh_symbols: continue` loop
already handles safely (zero alerts fire that cycle, not a crash).

scheduler.py can't be imported directly in this test environment — source-scanned, matching
this repo's established convention for this file.
"""
import pathlib

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
).read_text()


def _check_signal_alerts_body() -> str:
    start = _SOURCE.index("def check_signal_alerts() -> None:")
    end = _SOURCE.index("\n\ndef ", start + 10)
    return _SOURCE[start:end]


_BODY = _check_signal_alerts_body()


def test_db_error_path_sets_a_sentinel_distinct_from_a_real_empty_result():
    """A DB error during the freshness query must be distinguishable from the query genuinely
    running and finding zero rows — both used to be silently identical."""
    except_section = _BODY[_BODY.index("except Exception as exc:\n                log.warning(\"signal_alert.freshness_check_failed\""):]
    except_section = except_section[:except_section.index("\n\n            #")]
    assert "price_rows = None" in except_section


def test_fail_open_fallback_requires_price_rows_to_be_empty_or_none():
    """The exact fix: the fallback that assumes fresh must ALSO check `not price_rows` — not
    just `not fresh_symbols and symbols` — so a non-empty, all-stale result doesn't take it."""
    assert "if not fresh_symbols and symbols and not price_rows:" in _BODY


def test_all_stale_with_real_rows_takes_a_distinct_branch_that_does_not_fail_open():
    """When price rows exist but none are fresh, the code must NOT re-populate fresh_symbols —
    it must fall into a separate branch that leaves the alert run suppressed for those symbols."""
    elif_idx = _BODY.index("elif not fresh_symbols and symbols:")
    following = _BODY[elif_idx:elif_idx + 400]
    assert "fresh_symbols = set(symbols)" not in following
    assert 'log.error("signal_alert.freshness_all_stale"' in following


def test_the_two_branches_are_mutually_exclusive_not_both_reachable():
    """Structural check: the all-stale branch must be an `elif` off the no-data branch, not a
    second independent `if` that could also fire and re-populate fresh_symbols afterward."""
    no_data_idx = _BODY.index("if not fresh_symbols and symbols and not price_rows:")
    elif_idx = _BODY.index("elif not fresh_symbols and symbols:")
    # The elif must immediately follow the if-branch's own body, not sit somewhere unrelated.
    between = _BODY[no_data_idx:elif_idx]
    assert between.count("\n") < 10, "the elif should sit directly after the if-branch's body"
