"""Regression tests for the UW rate-limit-counter sawtooth found in the 2026-09-06 deep audit
(priority item #13), in unusual_whales.py.

Before the fix, _RATE_LIMIT_COUNTER_KEY was a SINGLE Redis key with a flat 48h TTL set on the
first increment — INCR + expire-once-on-first-write. This accumulated for exactly 48h from
whenever it was first written, then vanished entirely and restarted from zero — not a genuine
rolling 48h window despite the key name and the dashboard's "429s (48h)" label. Verified live:
value 109184, TTL 3021s — within ~50 minutes that field would read 0 even with 429s continuing
at full rate. An admin checking during an ACTIVE incident, shortly after that flip, would see
"429s (48h): 0" — the exact condition the panel exists to reveal.

Fix: rebuilt as hourly buckets (key: prefix:%Y%m%d%H), summed over the trailing 48 buckets at
READ time via _read_rate_limit_count_48h() — a genuine rolling window, since old hours simply
age out of the summed range rather than the whole counter vanishing at once.

Also covers the sibling fix in the same commit: _CALL_COUNTER_TTL_S (the per-endpoint daily
call-volume counter) was 25h — set on the first write of each day's bucket, so a bucket first
written at 00:00:30 UTC expired 01:00:30 the NEXT day, meaning the dashboard's "Yesterday" field
read 0 for ~23 of every 24 hours. Extended to 49h.
"""
import sys
from unittest.mock import patch

import src.services.unusual_whales as uw


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}

    def get(self, key):
        val = self.store.get(key)
        return str(val) if val is not None else None

    def set(self, key, value):
        self.store[key] = int(value)


def test_read_rate_limit_count_48h_sums_all_hourly_buckets_in_the_window():
    """The core fix: the reader must SUM across multiple hourly-bucketed keys, not read a
    single key — this is what makes it a genuine rolling window instead of a sawtooth."""
    from datetime import datetime, timedelta, timezone

    fake_redis = _FakeRedis()
    now = datetime.now(timezone.utc)
    # Scatter counts across several hours within the 48h window.
    for hours_ago, count in [(0, 5), (1, 3), (10, 7), (47, 2)]:
        bucket = (now - timedelta(hours=hours_ago)).strftime("%Y%m%d%H")
        fake_redis.set(f"{uw._RATE_LIMIT_COUNTER_PREFIX}:{bucket}", count)

    with patch.object(uw, "_get_redis", return_value=fake_redis):
        total = uw._read_rate_limit_count_48h()

    assert total == 5 + 3 + 7 + 2


def test_read_rate_limit_count_48h_ignores_a_bucket_outside_the_window():
    """A bucket older than 48h must NOT be counted — this is a rolling window, not an
    unbounded accumulator either."""
    from datetime import datetime, timedelta, timezone

    fake_redis = _FakeRedis()
    now = datetime.now(timezone.utc)
    old_bucket = (now - timedelta(hours=49)).strftime("%Y%m%d%H")
    fake_redis.set(f"{uw._RATE_LIMIT_COUNTER_PREFIX}:{old_bucket}", 999)

    with patch.object(uw, "_get_redis", return_value=fake_redis):
        total = uw._read_rate_limit_count_48h()

    assert total == 0


def test_read_rate_limit_count_48h_returns_zero_with_no_data_rather_than_raising():
    fake_redis = _FakeRedis()
    with patch.object(uw, "_get_redis", return_value=fake_redis):
        assert uw._read_rate_limit_count_48h() == 0


def test_read_rate_limit_count_48h_fails_open_on_a_redis_exception():
    class _BrokenRedis:
        def get(self, key):
            raise ConnectionError("redis unavailable")

    with patch.object(uw, "_get_redis", return_value=_BrokenRedis()):
        assert uw._read_rate_limit_count_48h() == 0  # must not raise


def test_the_exact_sawtooth_scenario_no_longer_reads_zero_during_an_ongoing_incident():
    """THE CORE BUG, reconstructed: under the OLD single-key design, a key first incremented
    49 hours ago would have expired (48h flat TTL) even though 429s continued steadily every
    hour since. Under the NEW hourly-bucket design, the most recent hours are still counted
    even though the 49-hour-old bucket has aged out — the rolling window correctly reflects
    ongoing activity instead of reading 0."""
    from datetime import datetime, timedelta, timezone

    fake_redis = _FakeRedis()
    now = datetime.now(timezone.utc)
    # An ongoing incident: steady 429s every hour for the last 10 hours, but the counter's
    # very FIRST bucket (49h ago, now aged out) is what an old single-key design would have
    # anchored its now-expired TTL to.
    old_bucket = (now - timedelta(hours=49)).strftime("%Y%m%d%H")
    fake_redis.set(f"{uw._RATE_LIMIT_COUNTER_PREFIX}:{old_bucket}", 50)
    for hours_ago in range(10):
        bucket = (now - timedelta(hours=hours_ago)).strftime("%Y%m%d%H")
        fake_redis.set(f"{uw._RATE_LIMIT_COUNTER_PREFIX}:{bucket}", 4)

    with patch.object(uw, "_get_redis", return_value=fake_redis):
        total = uw._read_rate_limit_count_48h()

    assert total == 40  # 10 hours x 4 — the ongoing incident is correctly visible
    assert total > 0  # NOT the pre-fix "reads 0 during an active incident" bug


def test_call_counter_ttl_survives_the_entire_following_day():
    """AUD-UWCACHE-YESTERDAYTTL: _CALL_COUNTER_TTL_S must be > 48h so a day's bucket, written
    at any point during that day, survives being read as "yesterday" at any point during the
    FOLLOWING day too — the old 25h value failed this for all but the first ~1h after midnight
    UTC. 49h (matching the rate-limit bucket TTL fix above) comfortably covers this."""
    assert uw._CALL_COUNTER_TTL_S > 48 * 3600
    assert uw._CALL_COUNTER_TTL_S != 25 * 3600  # the exact pre-fix value


def test_rate_limit_bucket_ttl_is_comfortably_above_the_rolling_window_length():
    """Each hourly bucket's own TTL must outlive the full 48h window it could be summed
    within, so a bucket doesn't expire mid-window and silently undercount."""
    assert uw._RATE_LIMIT_COUNTER_BUCKET_TTL_S > uw._RATE_LIMIT_COUNTER_WINDOW_HOURS * 3600
