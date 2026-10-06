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
