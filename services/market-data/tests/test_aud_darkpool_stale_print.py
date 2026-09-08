"""AUD-DARKPOOL-STALEPRINT — the same dark-pool print re-emailed every 60 minutes.

Reported by the user from their inbox: two Dark Pool Activity emails 61 minutes apart
(3:41pm and 4:42pm), byte-identical — `NET $39,137,875 / 137,664 shares @ $284.30` and
`TSM $37,978,768 / 86,512 shares @ $439.00`.

They were not two events. The DB holds exactly ONE NET print and ONE TSM print, both executed
20:00-20:04 UTC. The same blocks were re-selected and re-sent.

TWO DEFECTS COMBINING:

  1. **No age filter.** `get_dark_pool_prints(symbol)` returns UW's rolling window — production
     carries 4+ days per symbol (NET 242 rows back to 2026-09-04, TSM 1,117) — and the candidate
     selection is `max(qualifying, key=premium)` with NO check on when the print executed. So
     the single biggest block a symbol has ever printed stayed "the" candidate indefinitely.

  2. **The cooldown key could not tell prints apart.** It was
     `stockai:dark_pool_alert_cooldown:{uid}:{symbol}` — user and symbol only. It suppressed for
     60 minutes and then let the SAME block through again.

The job runs EVERY 1 MINUTE against 32 candidate symbols, each with its own independent
60-minute timer — which is why the user saw a steady drip rather than an occasional repeat.

A worse case was latent: a TSM print from 2026-09-04 at **$50.4M** is LARGER than either of
today's, so while it remained in UW's window it would out-rank them and alert as though new.
"""
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

SCHED_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
).read_text()

import src.services.scheduler as sched  # noqa: E402

_NOW = datetime(2026, 9, 8, 23, 50, tzinfo=timezone.utc)


def _cutoff(minutes: int | None = None) -> datetime:
    return _NOW - timedelta(minutes=minutes or sched._DARK_POOL_PRINT_MAX_AGE_MINUTES)


class _Row:
    def __init__(self, executed_at):
        self.executed_at = executed_at


# ── The age filter ──────────────────────────────────────────────────────────────────────

def test_the_two_prints_that_spammed_the_user_are_now_filtered():
    """THE REPORTED BUG. Both executed ~3.8h before the duplicate email went out."""
    assert sched._dark_pool_print_is_recent(_Row("2026-09-08T20:04:39"), _cutoff()) is False
    assert sched._dark_pool_print_is_recent(_Row("2026-09-08T20:00:15"), _cutoff()) is False


def test_the_larger_stale_print_is_filtered_too():
    """The latent worse case: a TSM print from 2026-09-04 at $50.4M — LARGER than either of
    today's, so it would out-rank them and alert as though new."""
    assert sched._dark_pool_print_is_recent(_Row("2026-09-04T20:28:20"), _cutoff()) is False


def test_a_genuinely_new_print_still_alerts():
    """The filter must not suppress real activity — that would trade spam for silence."""
    fresh = (_NOW - timedelta(minutes=10)).isoformat()
    assert sched._dark_pool_print_is_recent(_Row(fresh), _cutoff()) is True


def test_a_print_exactly_at_the_boundary_is_admitted():
    """`>=` not `>` — a print landing exactly on the cutoff is new enough."""
    assert sched._dark_pool_print_is_recent(_Row(_cutoff().isoformat()), _cutoff()) is True


def test_the_window_covers_the_job_cadence_plus_the_cooldown():
    """90 minutes is not arbitrary: the job runs every 1 minute and the cooldown is 60, so the
    window must exceed the cooldown or a real print could expire before it is ever sent."""
    assert sched._DARK_POOL_PRINT_MAX_AGE_MINUTES > sched._DARK_POOL_ALERT_COOLDOWN_MINUTES


# ── Failure direction ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, "", "not-a-date", "2026-13-45T99:99:99"])
def test_an_undateable_print_fails_CLOSED(bad):
    """FAILS CLOSED deliberately. An alert we cannot date is one we cannot prove is new, and
    the entire point of this filter is to stop re-sending prints whose age was ignored. Cost of
    a false negative: one missed email. Cost of a false positive: the spam being fixed."""
    assert sched._dark_pool_print_is_recent(_Row(bad), _cutoff()) is False


def test_a_parse_failure_cannot_break_the_alert_job():
    """One malformed row must not take down alerts for the other 31 symbols."""
    fn = SCHED_SRC[SCHED_SRC.index("def _dark_pool_print_is_recent"):]
    fn = fn[:fn.index("\ndef ")]
    assert "except (ValueError, TypeError, AttributeError)" in fn


def test_a_naive_timestamp_is_treated_as_utc():
    """UW's ISO strings may omit the offset. Treating a naive value as local time would
    reintroduce a timezone bug inside the fix — the same class as AUD-EXIT-HKENTRYDATE."""
    naive = (_NOW - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    assert sched._dark_pool_print_is_recent(_Row(naive), _cutoff()) is True


def test_a_trailing_Z_offset_parses():
    """fromisoformat rejects a bare 'Z' on older Pythons; the source normalises it."""
    z = (_NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    assert sched._dark_pool_print_is_recent(_Row(z), _cutoff()) is True


def test_a_datetime_object_is_accepted_too():
    """Defensive: the persisted-row path yields datetimes, not strings."""
    assert sched._dark_pool_print_is_recent(_Row(_NOW - timedelta(minutes=5)), _cutoff()) is True


# ── The cooldown key must identify the PRINT ────────────────────────────────────────────

def test_the_cooldown_key_includes_the_print_timestamp():
    """The old key was (user, symbol) only, so it could not tell two prints apart — it
    suppressed for 60 minutes and then let the SAME block through."""
    i = SCHED_SRC.index("_cd_print_id = candidates[symbol].get")
    block = SCHED_SRC[i:i + 500]
    assert "{uid}:{symbol}:{_cd_print_id}" in block


def test_the_key_falls_back_when_a_print_has_no_timestamp():
    """Must degrade to the old (user, symbol) key rather than producing a key ending in 'None',
    which would collide across every undated print for that symbol."""
    i = SCHED_SRC.index("_cd_print_id = candidates[symbol].get")
    block = SCHED_SRC[i:i + 500]
    assert "if _cd_print_id else" in block
    assert 'f"stockai:dark_pool_alert_cooldown:{uid}:{symbol}"' in block


def test_the_cooldown_still_uses_nx_and_a_ttl():
    """The set must stay atomic (nx=True) so two concurrent runs cannot both send, and keep a
    TTL so per-print keys cannot grow without bound."""
    i = SCHED_SRC.index("cd_key = (")
    block = SCHED_SRC[i:i + 700]
    assert "nx=True" in block
    assert "ex=_DARK_POOL_ALERT_COOLDOWN_MINUTES * 60" in block


def test_redis_failure_still_fails_open_for_delivery():
    """A Redis hiccup must not silently drop a real alert — the pre-existing behaviour, kept."""
    i = SCHED_SRC.index("cd_key = (")
    block = SCHED_SRC[i:i + 900]
    assert "cooldown_ok_symbols.append(symbol)" in block.split("except Exception")[1][:200]


# ── The filter is wired into candidate selection ────────────────────────────────────────

def test_the_age_filter_is_applied_to_qualifying_prints():
    i = SCHED_SRC.index("qualifying = [")
    block = SCHED_SRC[i:i + 400]
    assert "_dark_pool_print_is_recent(r, _print_cutoff)" in block


def test_the_dollar_and_relative_bars_are_unchanged():
    """This fix adds a condition; it must not relax the two that already existed."""
    i = SCHED_SRC.index("qualifying = [")
    block = SCHED_SRC[i:i + 400]
    assert ">= _DARK_POOL_ALERT_MIN_PREMIUM" in block
    assert ">= _rel_floor" in block


def test_prints_are_still_persisted_BEFORE_filtering():
    """AUD-DARKPOOL-NOPERSIST: the baseline is built from ordinary prints, so filtering before
    persisting would destroy the distribution the relative bar measures against. The age filter
    must sit AFTER _persist_dark_pool_prints, not before it."""
    persist_i = SCHED_SRC.index("_persist_dark_pool_prints(session, stock_id, symbol, rows)")
    filter_i = SCHED_SRC.index("_dark_pool_print_is_recent(r, _print_cutoff)")
    assert persist_i < filter_i
