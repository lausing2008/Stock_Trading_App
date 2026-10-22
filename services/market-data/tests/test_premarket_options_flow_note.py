"""Premarket "Options flow" note (2026-10-22): composition from the snapshot's versioned
classification with source session and completeness; bullish/bearish only from independent
directional evidence. Real functions end to end: the stored-row fetch, the attention-list note
and the brief email; only the DB session and the mail send are stubbed."""
from datetime import date
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


def _session_with(rows):
    """Stored snapshot rows, as _fetch_recent_options_flow reads them."""
    session = MagicMock()
    latest = MagicMock(); latest.scalar_one_or_none.return_value = date(2026, 10, 22)
    result = MagicMock(); result.all.return_value = rows
    session.execute.side_effect = [latest, result]
    return session


def test_the_premarket_note_generation_path_end_to_end():
    """Stored rows -> _fetch_recent_options_flow -> _build_attention_list -> the brief email."""
    ex = _exact(3000, 1000)
    rows = [
        ("AAPL", 3.0, 3.0, ex["sentiment"], None, None, None, None, 3000, 1000, ex["evidence"], date(2026, 10, 22)),
        ("MSFT", 10.0, 500.0, "strongly_bullish", None, None, None, None, None, None, None, date(2026, 10, 8)),
        ("NVDA", 0.33, 0.33, "bearish", None, None, None, None, 1000, 3000, None, date(2026, 10, 8)),
    ]
    flow = sched._fetch_recent_options_flow(_session_with(rows))
    by = {f["symbol"]: f for f in flow}
    assert by["MSFT"]["flow"]["label"] == "unclassified"
    attention = sched._build_attention_list({"AAPL", "MSFT", "NVDA"}, {"AAPL": 1, "MSFT": 1, "NVDA": 1}, [], flow, False)
    reasons = {a["symbol"]: a["reasons"] for a in attention}
    assert set(reasons) == {"AAPL", "NVDA"}                     # MSFT: legacy label without volumes
    assert reasons["AAPL"][0] == "Options volume call-heavy (75% calls / 25% puts) · session 2026-10-22 · coverage complete · direction not established"
    assert reasons["NVDA"][0].startswith("Options volume put-heavy (25% calls / 75% puts) · snapshot 2026-10-08")
    sent = []
    with patch("src.services.email_service.send_email", lambda to, subj, html, text: sent.append((html, text)) or True):
        send_premarket_brief_email(to="u@example.com", date_str="d", market="US", macro_events=[], my_earnings=[],
                                   recent_reactions=[], options_flow=flow, attention_list=attention)
    html, text = sent[0]
    assert "bullish" not in text.lower() and "bearish" not in text.lower()
    assert "AAPL: C/P 3.00 · Options volume call-heavy" in text
    assert "MSFT: C/P 10.00 · Options volume unclassified · snapshot 2026-10-08, source session not recorded" in text
