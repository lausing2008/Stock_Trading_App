"""Premarket "Options flow" note (2026-10-22): composition from the snapshot's versioned
classification with source session and completeness; bullish/bearish only from independent
directional evidence. Real functions end to end: the stored-row fetch, the attention-list note
and the brief email; only the DB session and the mail send are stubbed."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.options_scope import describe_flow_snapshot, volume_composition
import src.services.scheduler as sched
from src.services.email_service import send_premarket_brief_email


def _exact(calls, puts, **ev):
    comp = volume_composition(calls, puts)
    evidence = {"composition_rule": comp["composition_rule"], "chain_as_of": "2026-10-22", "coverage_status": "partial",
                "archive_coverage_status": "complete", "sample_adequacy": comp["sample_adequacy"],
                "call_share": comp["call_share"], "directional_intent": "not_established", **ev}
    return {"sentiment": comp["activity_composition"], "call_volume": calls, "put_volume": puts,
            "evidence": evidence, "as_of": date(2026, 10, 22)}


@pytest.mark.parametrize("calls,puts,label,words,notable", [
    (3000, 1000, "call_heavy", "call-heavy (75% calls / 25% puts)", True),
    (1000, 3000, "put_heavy", "put-heavy (25% calls / 75% puts)", True),
    (1100, 1000, "mixed", "mixed (52% calls / 48% puts)", False),
])
def test_all_three_versioned_labels(calls, puts, label, words, notable):
    d = describe_flow_snapshot(_exact(calls, puts))
    assert (d["label"], d["version"], d["notable"]) == (label, "exact_v1", notable)
    assert d["text"] == f"Options volume {words} · session 2026-10-22 · coverage complete · direction not established"


def test_few_contracts_on_one_side_is_said_and_not_notable():
    d = describe_flow_snapshot(_exact(5000, 40))
    assert d["label"] == "call_heavy" and d["notable"] is False and "few contracts on one side" in d["text"]


@pytest.mark.parametrize("legacy,calls,puts,label", [
    ("strongly_bullish", 6000, 2000, "call_heavy"),
    ("bullish", 1296, 1000, "mixed"),          # the rounded ratio called this 1.30
    ("slightly_bearish", 700, 1000, "put_heavy"),
    ("neutral", 900, 1000, "mixed"),
])
def test_legacy_and_rounded_era_rows_are_relabelled_from_stored_volumes(legacy, calls, puts, label):
    d = describe_flow_snapshot({"sentiment": legacy, "call_volume": calls, "put_volume": puts,
                                "evidence": None, "as_of": date(2026, 10, 8)})
    assert (d["label"], d["version"]) == (label, "relabelled_from_stored_volumes")
    assert "snapshot 2026-10-08, source session not recorded" in d["text"]
    assert "coverage not recorded" in d["text"] and "relabelled from stored volumes" in d["text"]
    assert "bullish" not in d["text"] and "bearish" not in d["text"]


def test_a_rounded_era_row_with_evidence_but_no_rule_is_relabelled_too():
    row = {"sentiment": "call_heavy", "call_volume": 1296, "put_volume": 1000, "as_of": date(2026, 10, 15),
           "evidence": {"chain_as_of": "2026-10-15", "coverage_status": "unverified"}}
    d = describe_flow_snapshot(row)
    assert (d["label"], d["version"]) == ("mixed", "relabelled_from_stored_volumes")
    assert "session 2026-10-15 · coverage unverified" in d["text"]


def test_missing_data_is_unclassified_never_a_default_label():
    for row in ({}, {"sentiment": "bullish"}, {"sentiment": None, "call_volume": None, "put_volume": 5}):
        d = describe_flow_snapshot(row)
        assert d["label"] == "unclassified" and d["notable"] is False
        assert "bullish" not in d["text"] and "neutral" not in d["text"]


def test_direction_words_only_with_independent_directional_evidence():
    plain = describe_flow_snapshot(_exact(3000, 1000, directional_intent="bullish"))
    assert "direction not established" in plain["text"]          # no basis given -> not used
    backed = describe_flow_snapshot(_exact(3000, 1000, directional_intent="bullish",
                                           directional_basis="ask-side sweep events, 2026-10-22"))
    assert "direction bullish (ask-side sweep events, 2026-10-22)" in backed["text"]


def _session_with(rows, newest=date(2026, 10, 22)):
    """Stored snapshot rows, as _fetch_recent_options_flow reads them."""
    session = MagicMock()
    latest = MagicMock(); latest.scalar_one_or_none.return_value = newest
    result = MagicMock(); result.all.return_value = rows
    session.execute.side_effect = [latest, result]
    return session


# Brief on Fri 2026-10-23 08:00 ET: the Thu evening job (rows keyed 10-22) describes Wed 10-21.
BRIEF_AT = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def test_the_premarket_note_generation_path_end_to_end():
    """Stored rows -> _fetch_recent_options_flow -> _build_attention_list -> the brief email."""
    ex = _exact(3000, 1000, chain_as_of="2026-10-21")
    rows = [
        ("AAPL", 3.0, 3.0, ex["sentiment"], None, None, None, None, 3000, 1000, ex["evidence"], date(2026, 10, 22)),
        ("MSFT", 10.0, 500.0, "strongly_bullish", None, None, None, None, None, None, {"chain_as_of": "2026-10-21"}, date(2026, 10, 22)),
        ("NVDA", 0.33, 0.33, "bearish", None, None, None, None, 1000, 3000, {"chain_as_of": "2026-10-21"}, date(2026, 10, 22)),
        # A pre-fix row keyed by run date but carrying an older session is never shown as current.
        ("ZZZ", 3.0, 3.0, "call_heavy", None, None, None, None, 3000, 1000, {"chain_as_of": "2026-10-15"}, date(2026, 10, 22)),
    ]
    flow = sched._fetch_recent_options_flow(_session_with(rows, date(2026, 10, 22)), now=BRIEF_AT)
    assert "ZZZ" not in {f["symbol"] for f in flow}
    by = {f["symbol"]: f for f in flow}
    assert by["MSFT"]["flow"]["label"] == "unclassified"
    attention = sched._build_attention_list({"AAPL", "MSFT", "NVDA"}, {"AAPL": 1, "MSFT": 1, "NVDA": 1}, [], flow, False)
    reasons = {a["symbol"]: a["reasons"] for a in attention}
    assert set(reasons) == {"AAPL", "NVDA"}                     # MSFT: legacy label without volumes
    assert reasons["AAPL"][0] == "Options volume call-heavy (75% calls / 25% puts) · session 2026-10-21 · coverage complete · direction not established"
    assert reasons["NVDA"][0].startswith("Options volume put-heavy (25% calls / 75% puts) · session 2026-10-21")
    sent = []
    with patch("src.services.email_service.send_email", lambda to, subj, html, text: sent.append((html, text)) or True):
        send_premarket_brief_email(to="u@example.com", date_str="d", market="US", macro_events=[], my_earnings=[],
                                   recent_reactions=[], options_flow=flow, attention_list=attention)
    html, text = sent[0]
    assert "bullish" not in text.lower() and "bearish" not in text.lower()
    assert "AAPL: C/P 3.00 · Options volume call-heavy" in text
    assert "MSFT: C/P 10.00 · Options volume unclassified · session 2026-10-21" in text


def test_the_brief_shows_nothing_when_the_newest_snapshot_is_stale():
    """Last night's run is missing on the Oct 23 brief: no rows, never an older run shown as current."""
    session = _session_with([], newest=date(2026, 10, 21))
    assert sched._fetch_recent_options_flow(session, now=BRIEF_AT) == []
    assert session.execute.call_count == 1          # never read the stale rows


# ── the EOD job: session-keyed, current-only, every skip recorded ─────────────────────────

def _run_job(results, now, symbols=(("1", "AAA"), ("2", "BBB"), ("3", "CCC"))):
    from types import SimpleNamespace
    from src.services import options_flow_snapshot as OFS
    written, captured, redis = [], [], {}
    sess = MagicMock()
    class _SL:
        def __enter__(self): return sess
        def __exit__(self, *a): return False
    def compute(sym):
        r = results[sym]
        return (SimpleNamespace(evidence={"chain_as_of": r}), None) if r[:2] == "20" else (None, r)
    with patch.object(sched, "SessionLocal", _SL), patch.object(sched, "_bounded_options_flow_symbols", lambda s: list(symbols)), \
         patch.object(sched, "capture_option_chain_history", lambda syms, a, b, skip_existing: captured.append((tuple(syms), a, b)) or {"days_done": 1}), \
         patch.object(OFS, "compute_options_flow_with_reason", compute), \
         patch.object(OFS, "upsert_options_flow_snapshot", lambda s, sid, r, as_of: written.append((sid, as_of))), \
         patch.object(sched.time, "sleep", lambda *_: None), patch.object(sched, "_record_job_status", lambda *a, **k: redis.setdefault("status", a[1])), \
         patch.object(sched, "_get_redis", lambda: SimpleNamespace(set=lambda k, v: redis.__setitem__(k, v),
                                                                   delete=lambda *k: redis.__setitem__("deleted", list(k)))):
        detail = sched.compute_options_flow_snapshots_eod(now=now)
    return detail, written, captured, redis


def test_job_writes_only_the_expected_session_keyed_by_that_session():
    now = datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc)        # Thu 17:00 ET -> expects Wed 10-21
    detail, written, captured, redis = _run_job({"AAA": "2026-10-21", "BBB": "2026-10-15", "CCC": "no_options_listed"}, now)
    assert detail["expected_session"] == "2026-10-21"
    assert written == [("1", date(2026, 10, 22))]                    # run date; evidence names the 10-21 session
    assert detail["stale_source_session"] == {"BBB": "2026-10-15"}   # never relabelled as current
    assert detail["unavailable"] == {"CCC": "no_options_listed"}
    assert captured == [(("AAA", "BBB", "CCC"), date(2026, 10, 21), date(2026, 10, 21))]
    assert redis["status"] == "partial" and "scheduler:job:options_flow_eod:detail" in redis   # BBB is stale
    from src.services.options_dominance import flow_cache_key
    assert redis["deleted"] == [flow_cache_key(sym) for sym in ("AAA", "BBB", "CCC")]


def test_monday_job_expects_friday_and_skips_holidays():
    from src.services.options_flow_snapshot import expected_flow_session
    assert expected_flow_session(date(2026, 10, 26)) == date(2026, 10, 23)       # Mon -> Fri
    assert expected_flow_session(date(2026, 11, 27)) == date(2026, 11, 25)       # Fri after Thanksgiving -> Wed
    detail, written, _, redis = _run_job({"AAA": "2026-10-23", "BBB": "2026-10-23", "CCC": "2026-10-23"},
                                         datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc))
    assert len(written) == 3 and redis["status"] == "ok"


def test_a_symbol_without_listed_options_does_not_make_the_run_partial():
    now = datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc)
    _, written, _, redis = _run_job({"AAA": "2026-10-21", "BBB": "2026-10-21", "CCC": "no_options_listed"}, now)
    assert len(written) == 2 and redis["status"] == "ok"
    _, _, _, redis = _run_job({"AAA": "2026-10-21", "BBB": "2026-10-21", "CCC": "chain_read_failed"}, now)
    assert redis["status"] == "partial"                               # a real failure still counts


def test_brief_after_a_holiday_reads_the_holiday_run():
    """Fri 2026-11-27: the Thanksgiving (Thu) cron run is keyed 11-26 and describes Wed 11-25."""
    rows = [("AAA", 3.0, 3.0, "call_heavy", None, None, None, None, 3000, 1000, {"chain_as_of": "2026-11-25"}, date(2026, 11, 26))]
    flow = sched._fetch_recent_options_flow(_session_with(rows, date(2026, 11, 26)),
                                            now=datetime(2026, 11, 27, 13, 0, tzinfo=timezone.utc))
    assert [f["symbol"] for f in flow] == ["AAA"]
    from src.services.options_flow_snapshot import last_job_day
    assert last_job_day(date(2026, 10, 26)) == date(2026, 10, 23)        # Monday brief -> Friday run


def test_a_provider_excluded_symbol_is_structural_in_the_snapshot_job():
    now = datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc)
    detail, written, captured, redis = _run_job({"AAA": "2026-10-21", "BRK-A": "2026-10-15"}, now,
                                                symbols=(("1", "AAA"), ("2", "BRK-A")))
    assert detail["unavailable"] == {"BRK-A": "provider_excluded"} and detail["stale_source_session"] == {}
    assert captured[0][0] == ("AAA",) and redis["status"] == "ok"
