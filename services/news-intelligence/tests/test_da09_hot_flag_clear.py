"""DA-09: minor unrelated news could erase an active material-negative risk brake.

THE DEFECT. The clear path was intended for corrections and retractions. Its actual condition
was "any inserted, classified, non-macro story for a symbol whose flag is currently negative" —
it did not require positive or neutral sentiment, correction semantics, a link to the original
event, or even a publication time later than the flagged one. The audit's reproduction inserts
"Unrelated minor negative story", classified negative and non-material, and watches the flag
clear. The signal engine uses that flag to compress bullish fused scores, so removing it changes
trading evidence on the strength of a story that said nothing good.

TWO GUARDS, both answerable from what is already stored:

  SENTIMENT — a negative story is not evidence that a negative situation improved.
  RECENCY   — an older article ingested late is a clock artefact, not news. Missing or
              unparseable timestamps fail CLOSED, because "I cannot tell which came first" is
              not grounds for removing a risk brake.

HONESTLY INCOMPLETE, and the audit says so itself: an unrelated POSITIVE story can still clear
an unresolved adverse event, because nothing links a story to the event it supposedly resolves.
The real remedy — event IDs, materiality, supersession and expiry, with active events aggregated
per symbol instead of one last-writer-wins flag — needs a schema change and is deliberately not
attempted in passing. What is fixed here is the case where the clearing story is itself bad news.

ALSO FIXED: `bool(item.get("is_material", False))`. An LLM can return the STRING "false", which
is valid JSON and which Python considers TRUE — marking a headline material and setting a brake
on the strength of a model saying the opposite.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.services import storage as S
from src.services.classify import _coerce_bool

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
OLDER = NOW - timedelta(hours=3)
NEWER = NOW + timedelta(hours=1)


def _cls(sentiment="positive", material=False, category="company"):
    return {"sentiment_label": sentiment, "is_material": material,
            "category": category, "sentiment_score": 50}


def _with_flag(ts=NOW, sentiment="negative"):
    payload = {"headline": "Original bad news", "sentiment_label": sentiment,
               "ts": ts.isoformat()}
    return patch.object(S, "_current_hot_payload", lambda _s: payload)


# ── The reported failure ─────────────────────────────────────────────────────

def test_an_unrelated_NEGATIVE_story_can_no_longer_clear_the_brake():
    """The audit's own reproduction: negative, non-material, and it cleared the flag."""
    with _with_flag():
        assert S._may_clear_negative_flag("AAPL", _cls(sentiment="negative"), NEWER) is False


def test_a_negative_story_cannot_clear_even_when_it_is_material():
    """Materiality is not the axis — a material negative story is MORE reason to keep the
    brake, not less."""
    with _with_flag():
        assert S._may_clear_negative_flag("AAPL", _cls(sentiment="negative", material=True), NEWER) is False


# ── Recency ──────────────────────────────────────────────────────────────────

def test_an_older_article_ingested_late_cannot_clear_a_newer_flag():
    """Backfills and slow feeds deliver old stories at any time. Clearing on one is a clock
    artefact, not evidence the situation resolved."""
    with _with_flag(ts=NOW):
        assert S._may_clear_negative_flag("AAPL", _cls(), OLDER) is False


def test_a_story_published_after_the_flagged_event_may_clear():
    with _with_flag(ts=NOW):
        assert S._may_clear_negative_flag("AAPL", _cls(), NEWER) is True


def test_a_story_published_at_the_same_instant_may_clear():
    with _with_flag(ts=NOW):
        assert S._may_clear_negative_flag("AAPL", _cls(), NOW) is True


def test_a_naive_timestamp_is_treated_as_utc_rather_than_crashing():
    """published_at arrives from several feeds with inconsistent tz handling; a comparison that
    raised here would propagate into the ingest loop."""
    with _with_flag(ts=NOW):
        naive = NEWER.replace(tzinfo=None)
        assert S._may_clear_negative_flag("AAPL", _cls(), naive) is True


# ── Failing closed ───────────────────────────────────────────────────────────

def test_a_missing_publication_time_fails_closed():
    """"I cannot tell which came first" is not grounds for removing a risk brake."""
    with _with_flag():
        assert S._may_clear_negative_flag("AAPL", _cls(), None) is False


def test_an_unparseable_timestamp_fails_closed():
    with _with_flag():
        assert S._may_clear_negative_flag("AAPL", _cls(), "not-a-date") is False


def test_a_flag_with_no_timestamp_fails_closed():
    payload = {"headline": "old format", "sentiment_label": "negative"}
    with patch.object(S, "_current_hot_payload", lambda _s: payload):
        assert S._may_clear_negative_flag("AAPL", _cls(), NEWER) is False


def test_no_flag_present_means_nothing_to_clear():
    with patch.object(S, "_current_hot_payload", lambda _s: None):
        assert S._may_clear_negative_flag("AAPL", _cls(), NEWER) is False


def test_a_neutral_story_may_clear():
    """Neutral IS evidence the situation moved on — that was the original intent of the path."""
    with _with_flag():
        assert S._may_clear_negative_flag("AAPL", _cls(sentiment="neutral"), NEWER) is True


def test_a_missing_sentiment_label_is_treated_as_neutral_not_negative():
    with _with_flag():
        assert S._may_clear_negative_flag("AAPL", {"category": "company"}, NEWER) is True


# ── The LLM boolean ──────────────────────────────────────────────────────────

def test_the_string_false_is_not_material():
    """`bool("false")` is True in Python. An LLM emitting that string would set a risk brake on
    the strength of a model saying the opposite."""
    assert _coerce_bool("false") is False
    assert _coerce_bool("False") is False
    assert _coerce_bool("FALSE") is False


def test_real_truth_values_survive():
    for truthy in (True, 1, "true", "True", "yes", "1"):
        assert _coerce_bool(truthy) is True, truthy
    for falsy in (False, 0, "", "no", "0", None, "maybe"):
        assert _coerce_bool(falsy) is False, falsy


def test_an_unexpected_type_is_not_material_rather_than_truthy():
    """A dict or list here means the model ignored the schema; that is not a reason to flag."""
    for weird in ({"a": 1}, ["x"], object()):
        assert _coerce_bool(weird) is False


# ── The guards must actually be WIRED IN ─────────────────────────────────────
#
# Everything above exercises the two helpers directly. A helper that nothing calls is
# decoration: removing `_may_clear_negative_flag(...)` from the clear condition, and reverting
# classify.py to `bool(...)`, both left every test above green. These assert the call sites.

def test_the_clear_path_consults_the_guard():
    import inspect
    import re

    src = inspect.getsource(S.persist_news_items)
    clear = src[src.index("_clear_hot(sym)") - 900:src.index("_clear_hot(sym)")]
    assert "_may_clear_negative_flag(" in clear, "the clear branch no longer consults the guard"
    # It must GATE the clear, not merely be mentioned nearby.
    assert re.search(r"and\s+_may_clear_negative_flag\(", clear)


def test_the_classifier_coerces_rather_than_trusting_python_truthiness():
    import inspect

    from src.services import classify as C
    src = inspect.getsource(C)
    assert '"is_material": _coerce_bool(' in src
    assert '"is_material": bool(' not in src


def test_the_guard_is_the_last_condition_so_the_cheap_checks_run_first():
    """Not correctness, but it reads Redis — placing it after the category and sentiment checks
    keeps it off the path for every macro or non-flagged story."""
    import inspect

    src = inspect.getsource(S.persist_news_items)
    clear = src[src.index("elif ("):src.index("_clear_hot(sym)")]
    assert clear.index('cls["category"] != "macro"') < clear.index("_may_clear_negative_flag(")
    assert clear.index("_current_hot_sentiment(sym)") < clear.index("_may_clear_negative_flag(")
