"""AUD-NEWSBATCH — a 5-second INACTIVITY timeout was mistaken for a latency bound.

MEASURED IN PRODUCTION over 24h on 2026-10-05: 88.6% of classification calls carried a single
headline, average batch 1.14 against a classifier that accepts 8, and single-headline calls
alone accounted for 219,247 tokens — 85.8% of news classification spend.

The cause is in the stream loop, not the classifier. `await asyncio.wait_for(ws.recv(),
timeout=5.0)` restarts its timer on every message received, so it measures SILENCE rather than
how long a headline has waited. Headlines arrive sparsely: one lands, five quiet seconds pass,
and it is sent alone — having had no opportunity to wait for a second item.

A latency bound is a statement about an ITEM ("no headline waits more than N seconds"), so the
deadline now runs on the oldest buffered item.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.batching import (  # noqa: E402
    MAX_BATCH, MAX_LATENCY_SECONDS, URGENT_LATENCY_SECONDS,
    deadline_for, should_flush, wait_timeout)


def test_the_buffer_size_matches_the_classifier_chunk():
    """It flushed at 5 against a classifier that takes 8, capping batches below the limit."""
    from services.classify import _BATCH_SIZE
    assert MAX_BATCH == _BATCH_SIZE == 8


def test_an_empty_buffer_never_flushes():
    assert should_flush(0, None, False) is False
    assert should_flush(0, 999.0, True) is False


def test_a_full_buffer_flushes_regardless_of_age():
    assert should_flush(MAX_BATCH, 0.0, False) is True


def test_a_lone_item_waits_for_company_rather_than_going_immediately():
    """The whole point: one item, no silence rule, so it stays until its deadline."""
    assert should_flush(1, 0.5, False) is False
    assert should_flush(1, 6.0, False) is False, \
        "six seconds of quiet no longer sends a single headline on its own"


def test_a_lone_item_still_goes_when_its_own_deadline_expires():
    """A bound that never expires is a queue, not a bound."""
    assert should_flush(1, MAX_LATENCY_SECONDS, False) is True
    assert should_flush(1, MAX_LATENCY_SECONDS + 1, False) is True


def test_a_tracked_headline_is_not_held_back_to_save_tokens():
    """It feeds the hot-news gate that suppresses BUY signals; delaying it trades correctness
    for money."""
    assert deadline_for(True) == URGENT_LATENCY_SECONDS
    assert URGENT_LATENCY_SECONDS < MAX_LATENCY_SECONDS
    assert should_flush(1, URGENT_LATENCY_SECONDS, True) is True
    assert should_flush(1, URGENT_LATENCY_SECONDS, False) is False


def test_the_wait_is_the_time_remaining_not_a_fixed_interval():
    """Otherwise a flush can overshoot its deadline by a whole poll interval."""
    assert wait_timeout(1, 0.0, False) == min(MAX_LATENCY_SECONDS, 5.0)
    assert wait_timeout(1, MAX_LATENCY_SECONDS - 1.0, False) == 1.0
    assert wait_timeout(1, 1.0, True) == URGENT_LATENCY_SECONDS - 1.0


def test_the_wait_never_goes_negative_past_the_deadline():
    assert wait_timeout(1, MAX_LATENCY_SECONDS + 10, False) == 0.0


def test_an_empty_buffer_just_polls():
    """Nothing can expire, so waking early would only burn cycles."""
    assert wait_timeout(0, None, False) == 5.0


# ---------------------------------------------------------------- the loop itself

_SRC = (Path(__file__).resolve().parents[1] / "src" / "services"
        / "alpaca_source.py").read_text()


def test_the_fixed_five_second_recv_timeout_is_gone():
    """Asserted against the AST, not the text: the first version of this test matched its own
    explanatory comment describing the removed timeout, and passed for the wrong reason."""
    import ast
    tree = ast.parse(_SRC)
    waits = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and ast.unparse(n.func).endswith("wait_for")]
    assert waits, "the loop still awaits the socket"
    for call in waits:
        kw = {k.arg: k.value for k in call.keywords}
        assert "timeout" in kw
        assert not isinstance(kw["timeout"], ast.Constant), \
            "a constant recv timeout measures silence, not how long an item has waited"
        assert ast.unparse(kw["timeout"]) == "_wait"


def test_the_loop_measures_the_oldest_item_not_silence():
    assert "buffered_at" in _SRC
    assert "time.monotonic() - buffered_at[0]" in _SRC


def test_the_old_hardcoded_flush_at_five_is_gone():
    import ast
    tree = ast.parse(_SRC)
    for cmp_ in (n for n in ast.walk(tree) if isinstance(n, ast.Compare)):
        txt = ast.unparse(cmp_)
        assert txt != "len(buffer) >= 5", "the flush threshold is no longer hardcoded below 8"
    assert "should_flush(len(buffer)" in _SRC


def test_the_buffer_is_flushed_on_shutdown_rather_than_dropped():
    """A stop event used to leave buffered headlines unstored."""
    assert '_flush("shutdown")' in _SRC


def test_an_unresolvable_universe_treats_news_as_urgent():
    """Fail towards promptness: the alternative silently delays exactly what matters."""
    seg = _SRC[_SRC.index("def _is_urgent"):]
    seg = seg[:seg.index("\n\n\n")] if "\n\n\n" in seg else seg
    assert "if not active:\n            return True" in seg
    assert "except Exception:\n        return True" in seg


# ---------------------------------------------------------------- per-call relevance context

def test_a_call_records_which_scopes_it_covered_and_which_articles():
    """Volume alone could not answer "how much of this was tracked stocks" or "was an article
    classified twice" — the 2026-10-05 review could not rule repeats in or out at all."""
    from services.classify import _call_context
    ctx = _call_context(["tracked", "market_context", "tracked"],
                        ["https://a/1", "https://a/2", "https://a/3"], 0, 3)
    assert ctx["scope_counts"] == {"tracked": 2, "market_context": 1}
    assert len(ctx["article_digests"]) == 3
    assert all(len(d) == 12 for d in ctx["article_digests"]), \
        "short digests: enough to detect a repeat, not a second copy of the index"


def test_the_context_covers_only_this_batch_not_the_whole_run():
    from services.classify import _call_context
    scopes = ["tracked"] * 8 + ["market_context"] * 4
    urls = [f"https://a/{i}" for i in range(12)]
    second = _call_context(scopes, urls, 8, 4)
    assert second["scope_counts"] == {"market_context": 4}
    assert len(second["article_digests"]) == 4


def test_the_same_url_digests_identically_from_any_batch_position():
    """A repeat is only detectable if the digest depends on the URL ALONE. The first version
    of this test compared the same URL at the same batch position, so it passed even when the
    position was mixed into the hash — and a repeat in a later batch would have gone unseen."""
    from services.classify import _call_context
    first = _call_context(None, ["https://a/1"], 0, 1)["article_digests"]
    urls = [f"https://a/{i}" for i in range(12)]
    urls[9] = "https://a/1"                       # the same article, in a later batch
    later = _call_context(None, urls, 8, 4)["article_digests"]
    assert first[0] in later, "the same URL must digest the same wherever it appears"


def test_missing_scope_is_counted_as_out_of_scope_not_dropped():
    from services.classify import _call_context
    ctx = _call_context([None, "tracked"], None, 0, 2)
    assert ctx["scope_counts"] == {"out_of_scope": 1, "tracked": 1}
    assert "article_digests" not in ctx


def test_storage_passes_scope_and_urls_through_to_the_call_log():
    src = (Path(__file__).resolve().parents[1] / "src" / "services"
           / "storage.py").read_text()
    seg = src[src.index("_results = classify_in_batches"):]
    seg = seg[:seg.index("\n            )") + 14]   # the whole call, not up to the first ")"
    assert "scopes=[_in_scope[i] for i in _to_classify]" in seg
    assert 'urls=[_new_items[i].get("url") for i in _to_classify]' in seg


# ---------------------------------------------------------------- budget enforcement wiring

_CLASSIFY = (Path(__file__).resolve().parents[1] / "src" / "services"
             / "classify.py").read_text()


def test_capacity_is_reserved_before_each_call_not_totalled_after():
    """Compared by AST node position, not by text index: the first version of this test
    searched the unparsed source and matched the docstring's mention of classify_headlines()
    rather than the call, so it compared a docstring against a reservation."""
    import ast
    fn = next(n for n in ast.walk(ast.parse(_CLASSIFY))
              if isinstance(n, ast.FunctionDef) and n.name == "classify_in_batches")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    reserve_at = [c.lineno for c in calls
                  if isinstance(c.func, ast.Name) and c.func.id == "reserve"]
    classify_at = [c.lineno for c in calls
                   if isinstance(c.func, ast.Name) and c.func.id == "classify_headlines"]
    assert reserve_at and classify_at, "both calls exist in the batcher"
    assert min(reserve_at) < min(classify_at), "a check after the call cannot refuse it"


def test_an_exhausted_budget_defers_and_never_labels_the_headline():
    """The headline is stored with NO label. It must never be recorded as neutral or not
    material because a cost ceiling was reached, and no existing flag is cleared."""
    import ast
    fn = next(n for n in ast.walk(ast.parse(_CLASSIFY))
              if isinstance(n, ast.FunctionDef) and n.name == "classify_in_batches")
    body = ast.unparse(fn)
    seg = body[body.index("if not res.allowed"):]
    seg = seg[:seg.index("out, actual")]
    assert "[None] * len(chunk)" in seg, "deferred items carry no classification at all"
    for word in ("neutral", "is_material", "sentiment_label"):
        assert word not in seg, f"a deferral must not synthesise {word!r}"


def test_reconciliation_happens_against_actual_usage_and_outcome():
    assert "return_usage=True" in _CLASSIFY
    assert "reconcile(res, actual, outcome=outcome)" in _CLASSIFY
    # An ambiguous outcome must not refund: a timeout may still have been charged.
    assert 'usage_out["_outcome"] = "ambiguous"' in _CLASSIFY


def test_the_reservation_is_an_upper_bound_not_an_estimate():
    """Charging a shortfall afterwards records an overshoot; it cannot prevent one."""
    assert "200 * len(chunk), scope=budget_scope" in _CLASSIFY
    assert "_estimate_tokens" not in _CLASSIFY.split("def _estimate_tokens")[0], \
        "the old point estimate is no longer what gets reserved"


def test_deferred_positions_are_returned_not_left_on_module_state():
    """Module state would not survive two concurrent polls."""
    assert "deferred_out: set | None = None" in _CLASSIFY
    assert "deferred_out.update(range(i, i + len(chunk)))" in _CLASSIFY
    storage = (Path(__file__).resolve().parents[1] / "src" / "services"
               / "storage.py").read_text()
    assert "deferred_out=_deferred_positions" in storage
    assert "_last_deferred" not in storage


def test_a_deferred_headline_records_why_so_a_retry_can_find_it():
    """URL dedup skips already-stored URLs, so an unclassified row would otherwise never be
    offered to the classifier again — a permanent, silent absence."""
    storage = (Path(__file__).resolve().parents[1] / "src" / "services"
               / "storage.py").read_text()
    assert "classification_deferred_reason=" in storage
    assert '"budget_exhausted"' in storage
    assert '"out_of_scope"' in storage, "never eligible is not the same as deferred"


def test_the_resolver_fallback_draws_on_its_own_scope():
    assert "SCOPE_RESOLVER_FALLBACK if resolver_degraded else SCOPE_NEWS_CLASSIFY" in _CLASSIFY
    storage = (Path(__file__).resolve().parents[1] / "src" / "services"
               / "storage.py").read_text()
    assert "resolver_degraded=not _resolver_ok" in storage


def test_a_deferral_is_recorded_so_the_accounting_shows_it():
    """Counting only issued calls would make a day of deferrals look like a quiet day."""
    assert "def log_llm_call_deferred" in _CLASSIFY
    assert 'status="deferred"' in _CLASSIFY
    assert '"deferred": True' in _CLASSIFY
