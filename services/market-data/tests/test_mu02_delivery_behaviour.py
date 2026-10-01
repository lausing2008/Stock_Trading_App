"""MU-02: the notification lifecycle, EXECUTED — not asserted by reading statement order.

The previous round checked "no marker write before the send" structurally. That is a claim about
source text, and the reviewer is right that it is not evidence: it cannot distinguish code that
behaves correctly from code that merely reads correctly, and one such assertion already let a
sabotage through.

These tests run the real `_send_early_earnings_stage` against a fake sender and a fake Redis and
assert on what actually happened:

    failed send        -> no marker, so the phase stays retryable
    successful send    -> marker written
    retry after success-> no duplicate email
    one phase failing  -> the other phases still go out

Plus the event-binding window, which decides whether a headline is even considered.
"""
import pathlib
import sys
import types

import pytest

SCHED_SRC = (pathlib.Path(__file__).resolve().parents[1]
             / "src/services/scheduler.py").read_text()


def _fn_src(name: str) -> str:
    i = SCHED_SRC.index(f"def {name}")
    nxt = SCHED_SRC.find("\ndef ", i + 10)
    return SCHED_SRC[i:nxt if nxt != -1 else len(SCHED_SRC)]


class FakeRedis:
    def __init__(self):
        self.store = {}

    def exists(self, k):
        return 1 if k in self.store else 0

    def setex(self, k, ttl, v):
        self.store[k] = (v, ttl)
        return True


class Recorder:
    """Stands in for email_service.send_email. `fail_for` makes specific subjects fail."""

    def __init__(self, fail_for=(), raise_for=()):
        self.sent = []
        self.fail_for = fail_for
        self.raise_for = raise_for

    def __call__(self, to, subject, html, text):
        if any(f in subject for f in self.raise_for):
            raise RuntimeError("controlled transport failure")
        if any(f in subject for f in self.fail_for):
            return False
        self.sent.append({"to": to, "subject": subject, "text": text})
        return True


def _make_sender(recorder):
    """Execute the REAL `_send_early_earnings_stage` with its dependencies supplied."""
    env = {
        "log": types.SimpleNamespace(info=lambda *a, **k: None,
                                     warning=lambda *a, **k: None,
                                     debug=lambda *a, **k: None),
    }
    # The function does `from .email_service import send_email` on entry — satisfy that import.
    mod = types.ModuleType("email_service_stub")
    mod.send_email = recorder
    sys.modules["__mu02_email_stub"] = mod
    src = _fn_src("_send_early_earnings_stage").replace(
        "from .email_service import send_email",
        "from __mu02_email_stub import send_email")
    exec(compile(src, "<sender>", "exec"), env)
    return env["_send_early_earnings_stage"]


USERS = {7: types.SimpleNamespace(email="r@example.invalid", username="reader")}
USER_SYMBOLS = {7: {"MU"}}
EVENT = "2026-09-30"


def _send(fn, rc, phase, subject_extra="", recorder_subject=None):
    fn(None, "MU", phase, "a headline", recorder_subject or f"subj-{phase}{subject_extra}",
       "body", USER_SYMBOLS, USERS, rc, EVENT)


# ── the four behavioural cases ──────────────────────────────────────────────────────────

def test_a_successful_send_writes_the_marker():
    rec = Recorder()
    rc = FakeRedis()
    _send(_make_sender(rec), rc, "results")
    assert len(rec.sent) == 1
    assert f"stockai:early_earnings_news:7:MU:{EVENT}:results" in rc.store


def test_the_marker_ttl_spans_more_than_one_calendar_day():
    """Executed, not read: the TTL the code actually writes must outlive a UTC-midnight crossing,
    so a phase delivered at 20:01 UTC is still marked when retries run the next morning. Asserted
    as a VALUE — a source-text check on the digits would survive the number being changed."""
    rec = Recorder()
    rc = FakeRedis()
    _send(_make_sender(rec), rc, "results")
    _value, ttl = rc.store[f"stockai:early_earnings_news:7:MU:{EVENT}:results"]
    assert ttl > 86400, "a one-day TTL can expire mid-incident while retries continue"
    assert ttl <= 7 * 86400, "but not so long that it outlives the event window"


def test_a_FAILED_send_writes_no_marker_so_the_phase_stays_retryable():
    """THE CASE THE STRUCTURAL ASSERTION COULD NOT PROVE."""
    rec = Recorder(fail_for=("subj-results",))
    rc = FakeRedis()
    _send(_make_sender(rec), rc, "results")
    assert rec.sent == []
    assert rc.store == {}, "a failed send must not suppress the phase"


def test_a_RAISING_send_also_writes_no_marker():
    rec = Recorder(raise_for=("subj-results",))
    rc = FakeRedis()
    _send(_make_sender(rec), rc, "results")
    assert rc.store == {}


def test_a_failed_send_can_be_retried_and_then_succeeds():
    rc = FakeRedis()
    failing = Recorder(fail_for=("subj-results",))
    _send(_make_sender(failing), rc, "results")
    assert rc.store == {}
    ok = Recorder()
    _send(_make_sender(ok), rc, "results")
    assert len(ok.sent) == 1
    assert f"stockai:early_earnings_news:7:MU:{EVENT}:results" in rc.store


def test_a_retry_after_success_does_not_send_again():
    rc = FakeRedis()
    rec = Recorder()
    fn = _make_sender(rec)
    _send(fn, rc, "results")
    _send(fn, rc, "results")
    assert len(rec.sent) == 1, "the same phase must not be delivered twice"


def test_one_failing_phase_does_not_stop_the_others():
    """Results fails; guidance and call must still go out, each with its own marker."""
    rc = FakeRedis()
    rec = Recorder(fail_for=("subj-results",))
    fn = _make_sender(rec)
    for phase in ("results", "guidance", "call"):
        _send(fn, rc, phase)
    subjects = [s["subject"] for s in rec.sent]
    assert "subj-guidance" in subjects and "subj-call" in subjects
    assert "subj-results" not in subjects
    assert f"stockai:early_earnings_news:7:MU:{EVENT}:results" not in rc.store
    assert f"stockai:early_earnings_news:7:MU:{EVENT}:guidance" in rc.store


def test_each_phase_gets_its_own_marker_and_its_own_email():
    rc = FakeRedis()
    rec = Recorder()
    fn = _make_sender(rec)
    for phase in ("preview", "results", "guidance"):
        _send(fn, rc, phase)
    assert len(rec.sent) == 3
    assert len(rc.store) == 3


def test_a_recipient_without_an_email_is_skipped_without_a_marker():
    rc = FakeRedis()
    rec = Recorder()
    fn = _make_sender(rec)
    fn(None, "MU", "results", "h", "s", "b", {9: {"MU"}},
       {9: types.SimpleNamespace(email=None, username="noemail")}, rc, EVENT)
    assert rec.sent == []
    assert rc.store == {}


# ── event binding ───────────────────────────────────────────────────────────────────────

def _binder():
    from datetime import date, datetime, timedelta, timezone
    env = {"date": date, "datetime": datetime, "timedelta": timedelta, "timezone": timezone}  # noqa
    exec(compile(_fn_src("_headline_belongs_to_event"), "<bind>", "exec"), env)
    return env["_headline_belongs_to_event"]


@pytest.mark.parametrize("published,expected", [
    ("2026-09-30T20:01:49+00:00", True),    # MU's actual result publication time (UTC)
    ("2026-09-29T12:00:00+00:00", True),    # a preview the day before
    ("2026-10-02T12:00:00+00:00", True),    # trailing call coverage
    ("2026-09-28T12:00:00+00:00", False),   # too early to be this event
    ("2026-10-03T12:00:00+00:00", False),   # too late
    (None, False),                          # unbindable -> rejected, never assumed
    ("not-a-timestamp", False),
])
def test_headline_event_binding_window(published, expected):
    from datetime import date
    assert _binder()(published, date(2026, 9, 30)) is expected


def test_an_unbindable_headline_is_rejected_rather_than_assumed():
    """Adding report_date to the KEY prevents collisions but does not stop an old headline being
    attached to the wrong event. A headline with no usable timestamp cannot be bound, so it is
    not used."""
    from datetime import date
    assert _binder()(None, date(2026, 9, 30)) is False


# ── the UTC-midnight case, executed against a REAL database ─────────────────────────────

import json as _json
import subprocess as _sp


def _boundary():
    proc = _sp.run([sys.executable,
                    str(pathlib.Path(__file__).resolve().parent / "_mu02_boundary_probe.py")],
                   capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    return _json.loads(proc.stdout.split("---PROBE-JSON---", 1)[1])


@pytest.fixture(scope="module")
def boundary():
    return _boundary()


def test_runs_either_side_of_utc_midnight_select_the_same_event(boundary):
    """THE REVIEWER'S CHECK. MU's result published 2026-09-30 20:01 UTC. A scheduler run that
    evening and another after UTC midnight must agree on WHICH event they are notifying about —
    executed through the real candidate query against a real database, not inferred from
    timestamp arithmetic."""
    assert boundary["before_midnight"] == {"MU": ["2026-09-30"]}
    assert boundary["after_midnight"] == {"MU": ["2026-09-30"]}
    assert boundary["same_event_date_for_MU"] is True


def test_the_dedup_key_is_identical_either_side_of_midnight(boundary):
    """So a phase delivered before midnight cannot be re-delivered after it."""
    assert boundary["keys_identical"] is True
    assert boundary["dedup_key_before"] == "stockai:early_earnings_news:7:MU:2026-09-30:results"


def test_a_future_earnings_event_is_not_selected(boundary):
    """The unbounded-window bug: a symbol reporting weeks ahead counted as pending."""
    assert boundary["future_event_excluded"] is True


def test_an_already_reported_event_is_not_selected(boundary):
    """Once eps_actual lands, check_earnings_reactions owns the alert for that symbol."""
    assert boundary["reported_event_excluded"] is True


def test_the_window_closes_after_two_days(boundary):
    """{yesterday, today} really is the window — the event drops out, so the alert cannot keep
    firing indefinitely on an old release."""
    assert boundary["two_days_later"] == {}


def test_two_pending_events_are_both_returned_so_the_caller_can_abstain(boundary):
    """Executed against the real query: the function must SURFACE the ambiguity rather than
    resolve it by picking the latest."""
    assert boundary["mu_has_two_candidates"] is True
    assert boundary["ambiguous_candidates"]["MU"] == ["2026-09-29", "2026-09-30"]


# ── issuer identity and period ambiguity: the window is a FRESHNESS filter, not binding ──

def test_a_different_issuer_in_the_same_window_is_rejected():
    """The date window cannot tell one company's print from another's. A nearby article about a
    different issuer, published in the same window, must not attach to this event."""
    from datetime import date
    b = _binder()
    assert b("2026-09-30T20:01:00+00:00", date(2026, 9, 30), "AMD", "MU") is False
    assert b("2026-09-30T20:01:00+00:00", date(2026, 9, 30), "MU", "MU") is True


def test_issuer_matching_is_case_insensitive_and_optional():
    from datetime import date
    b = _binder()
    assert b("2026-09-30T20:01:00+00:00", date(2026, 9, 30), "mu", "MU") is True
    # Absent issuer info falls back to the freshness window rather than rejecting outright.
    assert b("2026-09-30T20:01:00+00:00", date(2026, 9, 30), None, "MU") is True


def test_overlapping_event_windows_do_not_cross_attach():
    """Two events four days apart have overlapping ±windows. A headline in the overlap binds to
    whichever event is passed — so the caller's event choice, not the window, decides — and a
    headline outside an event's own window never binds to it."""
    from datetime import date
    b = _binder()
    early, late = date(2026, 9, 30), date(2026, 10, 3)
    # Windows are [event-1, event+2], so early covers 09-29..10-02 and late covers 10-02..10-05.
    # They overlap on EXACTLY ONE DAY, 10-02 — a headline there is genuinely ambiguous by date
    # alone, which is why date is a freshness filter and not an event binding.
    assert b("2026-10-02T12:00:00+00:00", early) is True
    assert b("2026-10-02T12:00:00+00:00", late) is True
    # Either side of the overlap, each event accepts only its own.
    assert b("2026-10-01T12:00:00+00:00", early) is True
    assert b("2026-10-01T12:00:00+00:00", late) is False
    assert b("2026-10-05T12:00:00+00:00", early) is False
    assert b("2026-10-05T12:00:00+00:00", late) is True


def test_a_results_headline_that_names_no_period_does_not_consume_the_results_phase():
    """AMBIGUITY MUST NOT EAT THE SLOT. A genuine result headline names its quarter — MU's did.
    One that names none cannot be bound to a fiscal period, so it is downgraded to `other`: no
    notification, and no consumption of the slot the real print needs."""
    import importlib.util as _il
    _src = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "earnings_phase.py"
    _sp = _il.spec_from_file_location("mu02_phase_amb", _src)
    _ep = _il.module_from_spec(_sp); sys.modules[_sp.name] = _ep; _sp.loader.exec_module(_ep)

    assert _ep.classify_earnings_phase("Acme Beats Estimates") == _ep.PHASE_OTHER
    assert _ep.phase_is_notifiable(_ep.classify_earnings_phase("Acme Beats Estimates")) is False
    # ...while a period-bearing result still classifies and still notifies.
    assert _ep.classify_earnings_phase("Acme Q3 EPS Of $1.10 Beats") == _ep.PHASE_RESULTS
    assert _ep.classify_earnings_phase("Acme Reports Fourth Quarter Results") == _ep.PHASE_RESULTS


def test_the_period_a_headline_names_is_preserved():
    import importlib.util as _il
    _src = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "earnings_phase.py"
    _sp = _il.spec_from_file_location("mu02_phase_per", _src)
    _ep = _il.module_from_spec(_sp); sys.modules[_sp.name] = _ep; _sp.loader.exec_module(_ep)
    assert _ep.extract_period("Micron Technology Q4 Adj EPS $33.42 Beats") == "Q4"
    assert _ep.extract_period("Micron Technology Sees Q1 Adj EPS $37.15-$39.15") == "Q1"
    assert _ep.extract_period("Acme Reports Fourth Quarter Results") == "Q4"
    assert _ep.extract_period("Acme Beats Estimates") is None


def test_the_fetcher_carries_the_issuer_through():
    i = SCHED_SRC.index("def _fetch_earnings_news_headlines")
    block = SCHED_SRC[i:i + 1500]
    assert 'i.get("symbol")' in block, "issuer identity must survive the fetch"


# ── period identity narrows a class; it does NOT establish event identity ───────────────

def _phase_mod():
    import importlib.util as _il
    _src = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "earnings_phase.py"
    _sp = _il.spec_from_file_location(f"mu02_ph_{id(object())}", _src)
    m = _il.module_from_spec(_sp); sys.modules[_sp.name] = m; _sp.loader.exec_module(m)
    return m


def test_a_full_year_release_is_not_excluded_for_lacking_a_quarter():
    """The correction. A valid release can report FULL-YEAR results and name no quarter at all;
    a quarter-only rule silently excluded those."""
    ep = _phase_mod()
    for h in ("Acme Reports Full-Year Results, EPS Beats",
              "Acme Full Year 2026 Results Top Estimates",
              "Acme FY2026 EPS Beats Consensus"):
        assert ep.classify_earnings_phase(h) == ep.PHASE_RESULTS, h
        ok, _ = ep.results_binding(h)
        assert ok is True


def test_a_results_headline_naming_no_period_is_excluded_WITH_A_REASON():
    """Kept out of the confirmed-results slot — and the reason is recorded, so a legitimate
    release cannot disappear invisibly."""
    ep = _phase_mod()
    ok, reason = ep.results_binding("Acme Beats Estimates")
    assert ok is False
    assert reason == "no_period_named"
    assert ep.classify_earnings_phase("Acme Beats Estimates") == ep.PHASE_OTHER


def test_the_named_period_and_year_are_recorded_but_never_matched_against_the_db_label():
    """MU's stored row for the 2026-09-30 release is labelled "Q3 2026" while the real headline
    says Q4 — an August fiscal year-end against a calendar-month-derived label. Matching the two
    would have REJECTED the very release this fix exists for, so the extracted period and year
    are carried for reconciliation only."""
    ep = _phase_mod()
    assert ep.extract_period("Micron Technology Q4 Adj EPS $33.42 Beats") == "Q4"
    assert ep.extract_fiscal_year("Micron Technology Q4 2026 Adj EPS Beats") == 2026
    assert ep.extract_fiscal_year("Micron Technology Q4 Adj EPS $33.42 Beats") is None
    # The scheduler must not compare the two.
    assert "fiscal_quarter ==" not in SCHED_SRC
    assert "EarningsEvent.fiscal_quarter" not in SCHED_SRC


def test_a_retrospective_article_naming_an_old_quarter_still_only_narrows():
    """Honest limit: a retrospective piece naming Q2 passes the period test. The date window and
    issuer check are what keep it out, not the period — which is why the period is explicitly
    not treated as event identity."""
    ep = _phase_mod()
    assert ep.extract_period("Looking Back At Acme Q2 Results") == "Q2"


def test_guidance_naming_a_future_quarter_stays_guidance():
    """MU's guidance named Q1 while the results named Q4. A future-quarter mention must not be
    read as a result for that quarter."""
    ep = _phase_mod()
    guidance = ("Micron Technology Sees Q1 Adj EPS $37.15-$39.15 vs $35.07 Est, "
                "Sees Sales $60.000B-$63.000B vs $56.553B Est")
    assert ep.classify_earnings_phase(guidance) == ep.PHASE_GUIDANCE
    assert ep.extract_period(guidance) == "Q1"
    assert ep.classify_earnings_phase("Acme Sees Q1 EPS Above Consensus") == ep.PHASE_GUIDANCE


def test_two_pending_events_make_the_symbol_ABSTAIN_rather_than_pick_one():
    """Overlapping events must abstain, not select arbitrarily: a wrong attribution marks the
    WRONG event's phase as delivered, which is worse than a missing alert."""
    # The count check itself is not pinned as source text (T401). What is asserted: the branch
    # exists, it abstains loudly, and — in the executed test below — the query really does return
    # both candidate events so the caller CAN abstain rather than silently pick one.
    i = SCHED_SRC.index("_candidates = _event_date_by_symbol[sym]")
    block = SCHED_SRC[i:i + 1600]
    assert "len(_candidates)" in block
    assert "early_earnings_news_abstained" in block
    assert "continue" in block


def test_the_candidate_query_returns_every_pending_event_not_just_the_latest():
    i = SCHED_SRC.index("def _pending_earnings_events")
    block = SCHED_SRC[i:i + 2000]
    assert "-> dict[str, list[date]]" in block
    assert "out.setdefault(sym, []).append(rd)" in block


def test_every_exclusion_path_records_a_reason():
    i = SCHED_SRC.index("_candidates = _event_date_by_symbol[sym]")
    block = SCHED_SRC[i:i + 3600]
    assert block.count("early_earnings_news_excluded") >= 2
    assert 'reason="issuer_or_freshness"' in block
    assert "results_not_bindable:" in block


# ── the unresolved classification risk, tested as a GAP rather than a fix ───────────────

def test_an_IN_WINDOW_retrospective_article_is_NOT_caught():
    """RECORDED AS AN UNRESOLVED RISK, not as a passing safeguard.

    Everything so far excludes OLD articles. A retrospective PUBLISHED TODAY about a past period
    passes every check: the issuer matches, the publication date is inside the freshness window,
    and it names a period. It is classified `results` and would occupy the confirmed-results slot
    for an event it is not about.

    This test asserts the gap EXISTS so it cannot be quietly assumed closed. If a future change
    genuinely closes it, this test fails and should be rewritten — that is the intended signal.
    """
    from datetime import date
    ep = _phase_mod()
    retro = "Revisiting Acme Q2 Results: EPS Beats In Hindsight"

    # Issuer: matches. Freshness: published today, inside the window. Period: named.
    assert _binder()("2026-09-30T14:00:00+00:00", date(2026, 9, 30), "ACME", "ACME") is True
    assert ep.extract_period(retro) == "Q2"
    ok, _ = ep.results_binding(retro)
    assert ok is True

    # ...and so it is classified as RESULTS. This is the open risk.
    assert ep.classify_earnings_phase(retro) == ep.PHASE_RESULTS, (
        "if this now returns something else, the gap may be closed — rewrite this test")


def test_the_unresolved_risk_is_documented_in_the_module():
    """So the next reader finds it where the logic lives, not only in an audit document."""
    ep = _phase_mod()
    assert "retrospective" in ep.KNOWN_UNRESOLVED_RISK.lower()
    assert "authoritative release identity" in ep.KNOWN_UNRESOLVED_RISK.lower()


def test_period_extraction_is_named_for_reconciliation_not_identity():
    ep = _phase_mod()
    doc = (ep.extract_period.__doc__ or "").lower()
    assert "reconciliation" in doc
    assert "not event identity" in doc


# ── abstentions are measured and surfaced, not merely logged ────────────────────────────

def test_abstentions_are_collected_and_summarised():
    i = SCHED_SRC.index("_candidates = _event_date_by_symbol[sym]")
    block = SCHED_SRC[i:i + 1200]
    assert "_abstained.append(" in block, "each abstention must be recorded, not just logged"
    assert "early_earnings_news_abstained_summary" in SCHED_SRC


def test_unresolved_releases_are_written_somewhere_inspectable():
    """A stale pending row would make the job abstain every cycle, silently suppressing a real
    result for as long as it survives. The unresolved set has to be reviewable."""
    assert '"stockai:early_earnings_news:unresolved"' in SCHED_SRC
    i = SCHED_SRC.index('"stockai:early_earnings_news:unresolved"')
    block = SCHED_SRC[i - 400:i + 400]
    assert "abstained" in block
    assert "as_of" in block, "the snapshot must say when it was taken"


def test_the_abstention_summary_names_the_suppression_hazard():
    i = SCHED_SRC.index("early_earnings_news_abstained_summary")
    block = SCHED_SRC[i:i + 600]
    assert "stale pending row" in block
