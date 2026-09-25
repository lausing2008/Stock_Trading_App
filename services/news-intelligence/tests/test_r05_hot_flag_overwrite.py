"""R05 (2026-09-24 follow-up audit): the DA-09 guard sat on the clear path only.

THREE DEFECTS, all in how the hot-news flag is written.

  (a) OVERWRITE BYPASSED THE GUARD ENTIRELY. Every material, non-macro story called
      `_mark_hot()` directly, which unconditionally `setex`'d over whatever was stored. A
      positive material story therefore removed an unresolved negative brake just as
      effectively as clearing it would have — and DA-09's guard, which exists to stop exactly
      that, was never consulted because this route never touched `_clear_hot()`. The audit's
      phrasing: prevent unrelated positive/neutral stories from removing an active negative
      flag, INCLUDING VIA OVERWRITE.

  (b) TWO CLOCKS. `_mark_hot()` wrote `datetime.now()` into `ts` — INGESTION time — and the
      guard compared a follow-up's PUBLICATION time against it. Ingestion always trails
      publication, so a correction genuinely published after the adverse article was rejected
      as "older" whenever that article was ingested late. A real correction, silently dropped.

  (c) READ/CHECK/WRITE WAS NOT ATOMIC. The clear branch read the flag three separate times and
      then deleted whatever happened to be present, so a newer adverse event arriving
      mid-decision was erased by a verdict that had never seen it.

STILL OPEN, and deliberately: an unrelated newer POSITIVE story can still clear an unresolved
event, because nothing links a story to the event it claims to resolve. That needs the event
table the audit describes (ids, materiality, supersession, expiry), not another guard. The
test at the bottom PINS that gap so it is not mistaken for closed.

The fake Redis is in conftest.py; read its docstring for what it can and cannot catch.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import FakeRedis  # noqa: E402
from src.services import storage as S  # noqa: E402

KEY = "stockai:hot_news:AAPL"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(hours=3)
LATER = NOW + timedelta(hours=2)


def _flag(r, *, sentiment="negative", published=NOW, ingested=None, headline="Adverse event"):
    ingested = ingested or published
    r.store[KEY] = json.dumps({
        "headline": headline, "sentiment_label": sentiment,
        "ts": ingested.isoformat(),
        "published_at": published.isoformat(),
        "ingested_at": ingested.isoformat(),
    })


def _stored(r):
    return json.loads(r.store[KEY])


# ── (a) the overwrite path ───────────────────────────────────────────────────

def test_an_older_material_positive_story_cannot_overwrite_an_unresolved_negative_flag():
    """THE CORE R05 CASE. This is the one the audit found: the story is material and non-macro,
    so it took the `_mark_hot()` route and never went near the clear guard."""
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r, published=NOW)
        S._mark_hot("AAPL", "Apple wins large contract", "positive", published_at=EARLIER)
    assert _stored(r)["sentiment_label"] == "negative", \
        "an older positive story replaced an unresolved negative brake"
    assert _stored(r)["headline"] == "Adverse event"


def test_a_positive_story_with_no_publication_time_cannot_overwrite():
    """Fails CLOSED, same rule as the clear path: "I cannot tell which came first" is not
    grounds for removing a risk brake."""
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r)
        S._mark_hot("AAPL", "Apple shares steady", "positive", published_at=None)
    assert _stored(r)["sentiment_label"] == "negative"


def test_a_neutral_story_is_held_to_the_same_bar_as_a_positive_one():
    """Neutral is "not negative", which is the axis that matters — it removes the brake just as
    completely as positive does."""
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r, published=NOW)
        S._mark_hot("AAPL", "Apple files routine 8-K", "neutral", published_at=EARLIER)
    assert _stored(r)["sentiment_label"] == "negative"


def test_a_delayed_real_correction_still_clears_the_flag():
    """The guard must not be so tight that nothing can ever resolve an event. A story published
    AFTER the adverse one is exactly the case the clear path exists for."""
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r, published=NOW)
        S._mark_hot("AAPL", "Apple denies report, regulator confirms", "positive",
                    published_at=LATER)
    assert _stored(r)["sentiment_label"] == "positive"
    assert _stored(r)["headline"] == "Apple denies report, regulator confirms"


def test_two_simultaneous_adverse_events_do_not_block_each_other():
    """A second piece of bad news must always be able to refresh the brake — the guard is about
    non-negative stories only. Blocking this would let the FIRST adverse event's TTL decide when
    a still-deteriorating situation stops being flagged."""
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r, published=NOW, headline="Supplier halts shipments")
        S._mark_hot("AAPL", "Second supplier halts shipments", "negative", published_at=NOW)
    assert _stored(r)["sentiment_label"] == "negative"
    assert _stored(r)["headline"] == "Second supplier halts shipments"


def test_an_older_negative_story_may_still_refresh_the_brake():
    """Recency gates REMOVING a brake, not setting one. An adverse story ingested late is still
    adverse; refusing it because of its timestamp would drop real risk information."""
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r, published=NOW)
        S._mark_hot("AAPL", "Older adverse report surfaces", "negative", published_at=EARLIER)
    assert _stored(r)["headline"] == "Older adverse report surfaces"


def test_a_positive_story_writes_freely_when_no_flag_is_set():
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        S._mark_hot("AAPL", "Apple raises guidance", "positive", published_at=NOW)
    assert _stored(r)["sentiment_label"] == "positive"


def test_a_positive_story_may_replace_a_positive_flag():
    """The guard is specifically about unresolved NEGATIVE events; ordinary updates are not
    restricted."""
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r, sentiment="positive", published=NOW)
        S._mark_hot("AAPL", "Apple raises guidance again", "positive", published_at=EARLIER)
    assert _stored(r)["headline"] == "Apple raises guidance again"


# ── (b) the two clocks ───────────────────────────────────────────────────────

def test_the_payload_records_publication_time_separately_from_ingestion_time():
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        S._mark_hot("AAPL", "h", "negative", published_at=EARLIER)
    p = _stored(r)
    assert p["published_at"] == EARLIER.isoformat(), "publication time must be stored as-is"
    ingested = datetime.fromisoformat(p["ingested_at"])
    assert (datetime.now(timezone.utc) - ingested).total_seconds() < 5
    # `ts` is the field signal-engine's age-decay reader already consumes; it must keep meaning
    # ingestion time, or that reader silently starts decaying from the wrong clock.
    assert p["ts"] == p["ingested_at"]


def test_a_correction_published_after_the_event_but_ingested_late_is_not_rejected():
    """DEFECT (b) IN ONE CASE. The adverse story was published at 12:00 but only ingested at
    17:00 (a slow feed). The correction was published at 14:00 — genuinely after the event. The
    old code compared 14:00 against the 17:00 INGESTION stamp and refused it."""
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r, published=NOW, ingested=NOW + timedelta(hours=5))
        assert S._may_clear_negative_flag(
            "AAPL", {"sentiment_label": "positive"}, NOW + timedelta(hours=2)) is True


def test_a_legacy_flag_without_publication_time_falls_back_to_ingestion_time():
    """Flags written before this change carry only `ts`. Falling back to it is STRICTER than
    the new comparison (ingestion >= publication), so the fallback never loosens the guard, and
    it drains within the flag's 2h TTL after deploy."""
    r = FakeRedis()
    r.store[KEY] = json.dumps({"headline": "old format", "sentiment_label": "negative",
                               "ts": NOW.isoformat()})
    with patch.object(S, "get_redis", lambda: r):
        assert S._may_clear_negative_flag("AAPL", {"sentiment_label": "positive"}, EARLIER) is False
        assert S._may_clear_negative_flag("AAPL", {"sentiment_label": "positive"}, LATER) is True


# ── (c) atomicity ────────────────────────────────────────────────────────────

def test_the_delete_is_refused_when_the_flag_changed_after_the_guard_read_it():
    """CONCURRENT FLAG REPLACEMENT. The guard approves a clear based on the flag it read; before
    the delete lands, a second ingest writes a NEWER adverse event. Deleting now would erase a
    brake nothing ever judged."""
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r, published=NOW, headline="First event")
        observed = r.store[KEY]
        _flag(r, published=LATER, headline="Second, worse event")   # the concurrent write
        S._clear_hot("AAPL", expect_raw=observed)
    assert KEY in r.store, "a newer adverse event was deleted by a stale verdict"
    assert _stored(r)["headline"] == "Second, worse event"


def test_the_delete_lands_when_the_flag_is_untouched():
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r)
        S._clear_hot("AAPL", expect_raw=r.store[KEY])
    assert KEY not in r.store


def test_a_lost_write_race_is_retried_rather_than_dropped():
    """A CAS miss must NOT mean "give up". Dropping the write loses a material NEGATIVE brake
    whenever an unrelated story lands in the same instant — the exact outcome this function
    exists to prevent. Re-read, re-judge, write again."""
    r = FakeRedis()
    calls = {"n": 0}
    real_eval = r.eval

    def flaky_eval(script, numkeys, *args):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate another ingest writing between our GET and our EVAL.
            _flag(r, published=NOW, headline="Concurrent adverse event")
            return 0
        return real_eval(script, numkeys, *args)

    with patch.object(S, "get_redis", lambda: r), patch.object(r, "eval", flaky_eval):
        S._mark_hot("AAPL", "Our adverse event", "negative", published_at=NOW)
    assert calls["n"] == 2, "the lost race must be retried, not dropped"
    assert _stored(r)["headline"] == "Our adverse event"


def test_the_retry_re_judges_rather_than_blindly_rewriting():
    """The retry must re-read and re-apply the guard. If a NEGATIVE flag appeared during the
    race, our positive story must now be refused — the whole point of re-deciding."""
    r = FakeRedis()
    calls = {"n": 0}
    real_eval = r.eval

    def flaky_eval(script, numkeys, *args):
        calls["n"] += 1
        if calls["n"] == 1:
            _flag(r, published=NOW, headline="Adverse event arrived mid-race")
            return 0
        return real_eval(script, numkeys, *args)

    with patch.object(S, "get_redis", lambda: r), patch.object(r, "eval", flaky_eval):
        S._mark_hot("AAPL", "Apple steady", "positive", published_at=EARLIER)
    assert _stored(r)["headline"] == "Adverse event arrived mid-race", \
        "the retry rewrote without re-applying the guard"


def test_the_retry_loop_is_bounded():
    """A permanently-contended key must not spin forever inside the ingest loop."""
    r = FakeRedis()
    calls = {"n": 0}

    def always_lose(script, numkeys, *args):
        calls["n"] += 1
        return 0

    with patch.object(S, "get_redis", lambda: r), patch.object(r, "eval", always_lose):
        S._mark_hot("AAPL", "h", "negative", published_at=NOW)   # must return, not hang
    assert calls["n"] == 3


def test_a_redis_failure_does_not_propagate_into_the_ingest_loop():
    class Broken(FakeRedis):
        def get(self, key):
            raise RuntimeError("redis down")

    with patch.object(S, "get_redis", lambda: Broken()):
        S._mark_hot("AAPL", "h", "negative", published_at=NOW)     # must not raise
        S._clear_hot("AAPL", expect_raw="x")                       # must not raise


# ── The guards must actually be WIRED IN ─────────────────────────────────────
#
# DA-05 and DA-09 both had sabotages survive because the helper was tested in isolation while
# nothing asserted the call site used it.

def test_the_material_branch_forwards_the_publication_time():
    """Without this argument `_mark_hot()` cannot judge recency and fails closed on EVERY
    overwrite — which looks like a working guard while actually being a broken feed."""
    import inspect

    src = inspect.getsource(S.persist_news_items)
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    call = code[code.index("_mark_hot(sym,"):]
    call = call[:call.index(")") + 1]
    assert 'raw.get("published_at")' in call, \
        "the material branch must forward the story's publication time to _mark_hot"


def test_mark_hot_consults_the_same_guard_the_clear_path_uses():
    """A SECOND copy of the rule here is exactly the drift this codebase keeps getting bitten
    by — and would let the two paths disagree about what resolves an event."""
    import inspect

    body = inspect.getsource(S._mark_hot)
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert "_may_clear_negative_flag(" in code, "the overwrite path must consult the guard"


def test_mark_hot_does_not_write_unconditionally_anywhere():
    """The original defect in one line: a bare `setex` over the live key, with nothing read
    first. Any reintroduction of one here re-opens R05 exactly."""
    import inspect

    body = inspect.getsource(S._mark_hot)
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert ".setex(" not in code, "the write must be the compare-and-swap, not a blind setex"
    assert "_HOT_SET_IF_UNCHANGED_LUA" in code


# ── What is still open ───────────────────────────────────────────────────────

def test_an_unrelated_newer_positive_story_STILL_clears_and_that_is_known():
    """NOT A PASSING GUARD — a pin on a gap the audit and the code both call out.

    Nothing links a story to the event it claims to resolve, so "Apple opens new store",
    published after an unresolved recall, reads as a resolution. Closing this needs the event
    table (ids, materiality, supersession, expiry) with active events aggregated per symbol
    instead of one last-writer-wins flag. If this test ever starts FAILING, the linkage has
    been built — delete it and write the real one."""
    r = FakeRedis()
    with patch.object(S, "get_redis", lambda: r):
        _flag(r, published=NOW, headline="Apple recalls product line")
        S._mark_hot("AAPL", "Apple opens flagship store in Mumbai", "positive",
                    published_at=LATER)
    assert _stored(r)["sentiment_label"] == "positive", \
        "the linkage gap appears to be closed — replace this pin with a real test"
