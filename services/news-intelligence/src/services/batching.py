"""When to flush the news buffer: an explicit AGE deadline, not an inactivity timeout.

THE DEFECT THIS CLOSES. The stream loop waited on `ws.recv()` with `timeout=5.0` and flushed on
TimeoutError. That timer restarts on every message received, so it measures SILENCE, not how
long an item has waited. In practice headlines arrive sparsely: one item lands, five seconds of
quiet follow, and it is sent alone — and it would never have waited for a second item however
long the buffer stayed small. Measured over 24h on 2026-10-05: 88.6% of calls carried a single
headline, average batch 1.14 against a classifier that takes 8, and those single-headline calls
alone accounted for 219,247 tokens (85.8% of news classification spend).

So the deadline is now on the OLDEST ITEM, which is the thing a latency bound is actually about:
"no headline waits more than N seconds", which is a promise that can be stated and checked.

PROMPTNESS IS PRESERVED WHERE IT MATTERS. A tracked-stock headline feeds the hot-news gate that
suppresses BUY signals on company-specific bad news; delaying that to save tokens would trade
money for correctness. Tracked items therefore carry a much shorter deadline than market
context, and the loop waits only until the earliest deadline that applies.
"""
from __future__ import annotations

#: Matches the classifier's own per-call chunk, so a full buffer is exactly one API call.
#: It was 5 against a classifier that takes 8, which capped batching below the limit.
MAX_BATCH = 8

#: How long an ordinary headline may wait to find company. The old behaviour was effectively
#: 5 seconds of silence; this is a real bound on the item, and longer because market-context
#: news has no gate waiting on it.
MAX_LATENCY_SECONDS = 20.0

#: A tracked-stock headline drives signal suppression. It batches only briefly.
URGENT_LATENCY_SECONDS = 3.0


def deadline_for(has_urgent: bool) -> float:
    return URGENT_LATENCY_SECONDS if has_urgent else MAX_LATENCY_SECONDS


def should_flush(buffer_size: int, oldest_age: float | None, has_urgent: bool) -> bool:
    """Flush when the buffer is full, or when its oldest item has waited long enough."""
    if buffer_size <= 0:
        return False
    if buffer_size >= MAX_BATCH:
        return True
    if oldest_age is None:
        return False
    return oldest_age >= deadline_for(has_urgent)


def wait_timeout(buffer_size: int, oldest_age: float | None, has_urgent: bool,
                 idle_poll: float = 5.0) -> float:
    """How long to wait for the next message before re-checking the flush condition.

    WITH AN EMPTY BUFFER this is just a poll interval — there is nothing whose age could
    expire, so waking early would only burn cycles. With items buffered it is the time REMAINING
    until their deadline, so a flush happens on time even if no further message ever arrives.
    """
    if buffer_size <= 0:
        return idle_poll
    if oldest_age is None:
        return idle_poll
    remaining = deadline_for(has_urgent) - oldest_age
    return max(0.0, min(remaining, idle_poll))
