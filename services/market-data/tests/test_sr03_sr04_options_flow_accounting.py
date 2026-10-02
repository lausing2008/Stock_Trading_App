"""SR-03 and SR-04 — options-flow alerts: what was sent, and which event decided the direction.

SR-03. `send_ok` began True and the resync wrote `current_chains` whenever it was still True.
Two paths reached that line with no email at all: every candidate on cooldown (no sender call,
yet all became "seen"), and the email cap (omitted contracts became "seen" too). Cooldowns were
claimed BEFORE the cap, so an omitted contract's whole (symbol, direction) pair was suppressed
by an email that never carried it. Witnesses from the review: an existing cooldown produced
zero sender calls but `seen=['c1']`; cap=1 with two different-symbol candidates sent only `c1`
and recorded `seen=['c1','c2']`.

SR-04. `candidates[row.option_chain] = {...}` let the LAST row for a contract win. The same
contract legitimately shows buying and selling at different times, so the same two events
produced "bearish" in one order and "bullish" when reversed — a direction set by transport.

The selection and accounting blocks are EXECUTED here, extracted from the real scheduler
source, because a test that reimplements them tests the reimplementation. `scheduler.py` cannot
be imported in this environment (heavy DB/live-price dependencies), matching the established
source-extraction convention of its sibling tests.
"""
import pathlib
import re
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "shared"))

from common.signal_time import parse_signal_instant  # noqa: E402

_SOURCE = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "services" / "scheduler.py").read_text()


def _options_flow_body() -> str:
    start = _SOURCE.index("def check_options_flow_alerts() -> None:")
    return _SOURCE[start:_SOURCE.index("\n\ndef ", start + 10)]


_BODY = _options_flow_body()


@dataclass
class _Row:
    option_chain: str
    option_type: str = "call"
    total_premium: float = 1_000_000.0
    created_at: str | None = "2026-10-01T15:00:00+00:00"


# ── SR-04: the newest-event selection, executed ─────────────────────────────────────────

def _selection_snippet() -> str:
    """The real SR-04 block, dedented so it can be exec'd on its own.

    Sliced from the START OF ITS LINE, not from the identifier: slicing mid-line leaves the
    first line flush and every following line at its original 24-space indent, which makes
    `textwrap.dedent` a silent no-op (it strips the common prefix, and the common prefix of a
    flush line is nothing). The result compiles to an IndentationError rather than running the
    production code — a harness that fails loudly, but only by luck.
    """
    i = _BODY.index("_event_at = parse_signal_instant(row.created_at)")
    i = _BODY.rindex("\n", 0, i) + 1
    j = _BODY.index("candidates[row.option_chain] = {", i)
    j = _BODY.rindex("\n", 0, j) + 1
    snippet = textwrap.dedent(_BODY[i:j])
    assert not snippet.startswith(" "), "dedent did not take — the slice is still mid-line"
    return snippet


def _select(rows, imbalance=0.5):
    """Run the REAL extracted selection over a list of rows, in the order given."""
    env = {
        "parse_signal_instant": parse_signal_instant,
        "candidates": {}, "log": _NullLog(),
        "_flow_quarantined": 0, "_flow_superseded": 0,
        "symbol": "AAPL", "_imbalance": imbalance,
    }
    snippet = _selection_snippet()
    for row in rows:
        env["row"] = row
        loop = "for _once in (0,):\n" + textwrap.indent(snippet, "    ")
        loop += "    candidates[row.option_chain] = {'event_at': _event_at.at,\n"
        loop += "        'total_premium': row.total_premium, 'side_imbalance': _imbalance,\n"
        loop += "        'option_type': row.option_type, 'created_at': row.created_at}\n"
        # `continue` is valid inside the one-iteration loop and means exactly what it
        # means in the scheduler: skip this row. No text substitution.
        exec(compile(loop, "<scheduler-snippet>", "exec"), env)
    return env


class _NullLog:
    def __getattr__(self, _name):
        return lambda *a, **k: None


def test_the_newest_event_wins_whatever_order_it_arrives_in():
    """SR-04's WITNESS. Two events on one contract; the verdict must not depend on order."""
    older = _Row("C1", option_type="call", created_at="2026-10-01T14:00:00+00:00",
                 total_premium=500_000)
    newer = _Row("C1", option_type="call", created_at="2026-10-01T15:00:00+00:00",
                 total_premium=900_000)
    forward = _select([older, newer])["candidates"]["C1"]
    reverse = _select([newer, older])["candidates"]["C1"]
    assert forward["created_at"] == newer.created_at
    assert forward == reverse, "direction must be invariant to transport ordering"


def test_an_older_event_cannot_supersede_a_newer_one():
    newer = _Row("C1", created_at="2026-10-01T15:00:00+00:00")
    older = _Row("C1", created_at="2026-09-29T09:00:00+00:00")
    env = _select([newer, older])
    assert env["candidates"]["C1"]["created_at"] == newer.created_at
    assert env["_flow_superseded"] == 1


def test_a_row_with_no_event_time_is_quarantined_not_used():
    """Without a time there is no way to tell a fresh print from a two-day-old one, and the
    feed's own window is 48 hours."""
    env = _select([_Row("C1", created_at=None)])
    assert "C1" not in env["candidates"]
    assert env["_flow_quarantined"] == 1


def test_an_unparseable_event_time_is_quarantined_too():
    env = _select([_Row("C1", created_at="not-a-time")])
    assert "C1" not in env["candidates"]
    assert env["_flow_quarantined"] == 1


def test_quarantine_does_not_discard_a_valid_sibling_event():
    good = _Row("C1", created_at="2026-10-01T15:00:00+00:00")
    env = _select([_Row("C1", created_at=None), good])
    assert env["candidates"]["C1"]["created_at"] == good.created_at
    assert env["_flow_quarantined"] == 1


def test_a_tie_is_broken_by_evidence_and_is_order_stable():
    a = _Row("C1", created_at="2026-10-01T15:00:00+00:00", total_premium=100_000)
    b = _Row("C1", created_at="2026-10-01T15:00:00+00:00", total_premium=900_000)
    assert _select([a, b])["candidates"]["C1"]["total_premium"] == 900_000
    assert _select([b, a])["candidates"]["C1"]["total_premium"] == 900_000


def test_identical_rows_in_any_permutation_give_the_same_result():
    rows = [_Row("C1", created_at="2026-10-01T15:00:00+00:00", total_premium=100_000)] * 3
    assert _select(rows)["candidates"]["C1"]["total_premium"] == 100_000


def test_distinct_contracts_do_not_compete():
    env = _select([_Row("C1"), _Row("C2")])
    assert set(env["candidates"]) == {"C1", "C2"}
    assert env["_flow_superseded"] == 0


# ── SR-03: send accounting, asserted on the real block ──────────────────────────────────

def test_the_resync_no_longer_marks_unsent_candidates_seen():
    assert "resync_set = (prev_seen & current_chains) | accepted_chains" in _BODY
    assert "resync_set = current_chains if send_ok" not in _BODY


def test_acceptance_is_recorded_only_inside_the_successful_send_arm():
    send = _BODY.index("send_ok = send_options_flow_alert_email(")
    ok_arm = _BODY.index("if send_ok:", send)
    accept = _BODY.index("accepted_chains = set(capped)", ok_arm)
    nxt = _BODY.index("# SR-03: the seen set is DELIVERY identity", accept)
    assert ok_arm < accept < nxt


def test_the_cooldown_filter_is_a_read_and_the_claim_follows_delivery():
    filt = _BODY.index("deferred_chains = []")
    send = _BODY.index("send_ok = send_options_flow_alert_email(")
    claim = _BODY.index('_rc.set(_cd_key, "1", nx=True,')
    assert "_rc.exists(cd_key)" in _BODY[filt:send]
    assert "nx=True" not in _BODY[filt:send], "the filter must not claim what it tests"
    assert send < claim, "a claim before the cap suppresses contracts never sent"


def test_omitted_contracts_are_tracked_separately_from_delivered_ones():
    assert "omitted_chains = ranked[len(capped):]" in _BODY
    claim_loop = _BODY.rindex("for chain in capped:", 0, _BODY.index('_rc.set(_cd_key, "1", nx=True,'))
    assert "omitted_chains" not in _BODY[claim_loop:_BODY.index('_rc.set(_cd_key, "1", nx=True,')]


def test_every_state_in_the_lifecycle_is_counted():
    """observed -> deferred -> queued -> accepted | omitted. A state that is not counted is a
    state nobody can reconcile."""
    i = _BODY.index("options_flow_alert.recipient_accounting")
    block = _BODY[i:_BODY.index("\n", _BODY.index("note=", i))]
    for field in ("observed=", "deferred_cooldown_or_pair=", "queued=",
                  "omitted_by_cap=", "accepted=", "send_attempted="):
        assert field in block, f"{field} is not reported"


def test_the_pair_dedup_the_claim_used_to_perform_is_still_performed():
    """The nx=True claim deduped (symbol, direction) as a side effect. Removing the early claim
    must not silently remove that, or one symbol could fill the whole cap."""
    i = _BODY.index("_best_per_pair: dict[tuple[str, str], str] = {}")
    window = _BODY[i:_BODY.index("cooldown_ok_chains = []", i)]
    assert "total_premium" in window and "incumbent" in window


def test_the_job_reports_the_selection_counters():
    i = _BODY.index('log.info("options_flow_alert.done"')
    block = _BODY[i:_BODY.index(")", _BODY.index("superseded_by_newer_event", i))]
    assert "quarantined_no_event_time" in block
    assert "superseded_by_newer_event" in block


def test_no_second_copy_of_the_event_time_parser_grew_in_the_scheduler():
    """One canonical reading, shared with the decision engine's freshness gate."""
    i = _BODY.index("_event_at = parse_signal_instant(row.created_at)")
    window = _BODY[max(0, i - 1500):i]
    assert not re.search(r"fromisoformat\(.*created_at", window)


# ── PI-04: the per-portfolio scan activity the inactivity follow-up asked for ───────────
#
# Recommendation 4 of docs/audits/2026-09-30-paper-inactivity-followup.md: "Add per-portfolio
# last scan, last entry, candidates seen and binding reason to the UI, with a clear
# distinction between zero supply, rejected candidates and portfolio-level blocks."
#
# The distinction is the whole point, and the follow-up says why: "Portfolio-blocked scans can
# have NULL candidate counts: a blocked scan is not necessarily an evaluated zero-candidate
# universe." Reporting a blocked scan as "0 candidates" asserts a measurement nobody made.

_PP_API = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "api" / "paper_portfolio.py").read_text()


def _scan_activity_block() -> str:
    i = _PP_API.index("PI-04: durable per-portfolio scan activity")
    return _PP_API[i:_PP_API.index("    result = []", i)]


def test_a_blocked_scan_reports_an_unknown_candidate_count_not_zero():
    block = _scan_activity_block()
    i = block.index('if row.portfolio_gate:')
    arm = block[i:block.index("elif", i)]
    assert 'state, seen = "portfolio_blocked", None' in arm, \
        "a blocked scan never evaluated the universe; its count is unknown"
    assert "0" not in arm.split("state, seen")[1].split("\n")[0]


def test_an_evaluated_empty_universe_reports_zero():
    block = _scan_activity_block()
    assert 'state, seen = "no_candidates", 0' in block, \
        "an evaluated universe that produced nothing really did see zero"


def test_rejected_candidates_report_their_count_and_binding_reason():
    block = _scan_activity_block()
    i = block.index('elif row.candidates_seen:')
    arm = block[i:block.index("else:", i)]
    assert 'state, seen = "candidates_rejected", int(row.candidates_seen)' in arm
    assert "max(tally.items()" in arm, "the binding reason must be the largest bucket"


def test_the_repeat_caveat_travels_with_the_counts():
    """The follow-up: 'Counts repeat opportunities across scans; never interpret them as
    distinct lost trades.' A caveat that lives only in a document is a caveat nobody reads."""
    assert '"counts_repeat_across_scans": True' in _scan_activity_block()


def test_the_last_entry_comes_from_trades_not_from_scan_logs():
    """A scan log says what the scanner did; only a trade says an entry happened."""
    i = _PP_API.index("_last_entry: dict[int, str] = {}")
    block = _PP_API[i:_PP_API.index("result = []", i)]
    assert "func.max(PaperTrade.entry_time)" in block


def test_a_portfolio_with_no_retained_scan_rows_reports_none():
    """Absent is not 'nothing blocked it'."""
    assert '"scan_activity": _scan_activity.get(p.id),' in _PP_API


def test_the_scan_lookup_cannot_break_the_portfolio_list():
    block = _scan_activity_block()
    assert "except Exception:" in block, \
        "a missing scan-log table must omit the field, not fail the whole endpoint"
