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
    # Behavioural (was a source-text check): odd types never raise, they fail closed.
    for odd in (12345, object(), b"2026-09-08", ["x"], "2026-13-45T99:99:99Z"):
        assert sched._dark_pool_print_is_recent(_Row(odd), _cutoff()) is False


def test_a_naive_timestamp_is_quarantined_not_assumed_utc():
    """Options review B4 (2026-10-22) REVERSES the earlier rule. Assuming UTC for a value with
    no offset guesses which instant it names; a guessed instant cannot prove a print is new.
    UW sends `Z` on every print (measured on 779,482 stored rows), so nothing real is lost."""
    naive = (_NOW - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    assert sched._dark_pool_print_is_recent(_Row(naive), _cutoff()) is False


def test_a_trailing_Z_offset_parses():
    """fromisoformat rejects a bare 'Z' on older Pythons; the source normalises it."""
    z = (_NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    assert sched._dark_pool_print_is_recent(_Row(z), _cutoff()) is True


def test_a_datetime_object_is_accepted_too():
    """Defensive: the persisted-row path yields datetimes, not strings."""
    assert sched._dark_pool_print_is_recent(_Row(_NOW - timedelta(minutes=5)), _cutoff()) is True


# ── The cooldown key must identify the PRINT ────────────────────────────────────────────

def test_the_key_prefers_the_provider_tracking_id():
    key, ttl = sched._dark_pool_alert_key(7, "TSM", {"tracking_id": "abc", "executed_at": "t", "premium": 5e6})
    assert key == "stockai:dark_pool_alert_cooldown:7:TSM:id:abc"
    assert ttl == sched._DARK_POOL_PRINT_KEY_TTL_SECONDS


def test_two_prints_in_the_same_second_get_different_keys():
    """AUD-DP-REPEAT-SEND: an execution-time-only key made the second print collide with the first."""
    k1, _ = sched._dark_pool_alert_key(7, "TSM", {"executed_at": "2026-11-10T15:00:00Z", "premium": 5e6})
    k2, _ = sched._dark_pool_alert_key(7, "TSM", {"executed_at": "2026-11-10T15:00:00Z", "premium": 2e6})
    assert k1 != k2


def test_the_key_falls_back_to_user_and_symbol_with_the_ordinary_cooldown():
    """No identity and no time: the original (user, symbol) key, never one ending in 'None'."""
    key, ttl = sched._dark_pool_alert_key(7, "TSM", {})
    assert key == "stockai:dark_pool_alert_cooldown:7:TSM"
    assert ttl == sched._DARK_POOL_ALERT_COOLDOWN_MINUTES * 60


def test_a_print_key_outlives_the_age_window():
    """AUD-DP-REPEAT-SEND (2026-11-10): the key used to expire at 60 minutes while the print
    stayed a candidate for 90, so the same print was emailed twice."""
    assert sched._DARK_POOL_PRINT_KEY_TTL_SECONDS > sched._DARK_POOL_PRINT_MAX_AGE_MINUTES * 60


def test_replaying_one_print_across_its_whole_window_sends_it_once():
    """Executed 10:00, first seen 10:05, then every minute until it ages out at 11:30."""
    executed = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
    cand = {"tracking_id": "p1", "executed_at": executed.isoformat(), "premium": 5e6}
    store: dict[str, datetime] = {}   # key -> expiry instant: a fake Redis SET NX EX
    sends = 0
    for minute in range(5, 92):
        now = executed + timedelta(minutes=minute)
        if not sched._dark_pool_print_is_recent(_Row(cand["executed_at"]),
                                                now - timedelta(minutes=sched._DARK_POOL_PRINT_MAX_AGE_MINUTES)):
            continue
        key, ttl = sched._dark_pool_alert_key(1, "TSM", cand)
        if key in store and store[key] > now:
            continue
        store[key] = now + timedelta(seconds=ttl)
        sends += 1
    assert sends == 1


def test_the_job_uses_the_identity_key_and_its_ttl():
    body = _dark_pool_body()
    assert "cd_key, _cd_ttl = _dark_pool_alert_key(uid, symbol, candidates[symbol])" in body
    assert 'if _rc.set(cd_key, "1", nx=True, ex=_cd_ttl):' in body
    assert '"tracking_id": getattr(biggest, "tracking_id", None),' in body


def _dark_pool_body() -> str:
    """The DARK POOL function's own source.

    These assertions used to search the whole file from `SCHED_SRC.index("cd_key = (")`, which
    is the first match ANYWHERE — and SR-03 (2026-10-02) introduced a `cd_key = (` in
    `check_options_flow_alerts`, which sits earlier in the file. The tests then read a
    different function's cooldown and failed, while the code they were written to protect was
    untouched. Scoping to the function under test is the fix; a whole-file index is an
    assertion about file layout, not about behaviour.
    """
    start = SCHED_SRC.index("def check_dark_pool_alerts() -> None:")
    return SCHED_SRC[start:SCHED_SRC.index("\n\ndef ", start + 10)]


def test_the_cooldown_still_uses_nx_and_a_ttl():
    """The set must stay atomic (nx=True) so two concurrent runs cannot both send, and keep a
    TTL so per-print keys cannot grow without bound."""
    body = _dark_pool_body()
    i = body.index("cd_key, _cd_ttl = _dark_pool_alert_key(")
    block = body[i:i + 700]
    assert "nx=True" in block
    assert "ex=_cd_ttl" in block


def test_redis_failure_still_fails_open_for_delivery():
    """A Redis hiccup must not silently drop a real alert — the pre-existing behaviour, kept."""
    body = _dark_pool_body()
    i = body.index("cd_key, _cd_ttl = _dark_pool_alert_key(")
    block = body[i:i + 900]
    assert "cooldown_ok_symbols.append(symbol)" in block.split("except Exception")[1][:200]


# ── The filter is wired into candidate selection ────────────────────────────────────────

def test_the_age_filter_is_applied_to_qualifying_prints():
    i = SCHED_SRC.index("qualifying = [")
    block = SCHED_SRC[i:i + 400]
    assert "_dark_pool_print_is_recent(r, _print_cutoff)" in block


def test_the_dollar_and_relative_bars_are_unchanged():
    """Execute the shared predicate; source substrings can pass on comments or dead code."""
    from src.services.option_evidence import dark_pool_qualifies

    assert dark_pool_qualifies(1_000_000, 200_000, 1_000_000, 5.0)
    assert not dark_pool_qualifies(999_999, 100_000, 1_000_000, 5.0)
    assert not dark_pool_qualifies(1_000_000, 250_000, 1_000_000, 5.0)


def test_prints_are_still_persisted_BEFORE_filtering():
    """AUD-DARKPOOL-NOPERSIST: the baseline is built from ordinary prints, so filtering before
    persisting would destroy the distribution the relative bar measures against. The age filter
    must sit AFTER _persist_dark_pool_prints, not before it."""
    persist_i = SCHED_SRC.index("_persist_dark_pool_prints(session, stock_id, symbol, rows)")
    filter_i = SCHED_SRC.index("_dark_pool_print_is_recent(r, _print_cutoff)")
    assert persist_i < filter_i


# ── AUD-DP-EMAIL-WORDING (2026-11-10) ───────────────────────────────────────────────────────

def _dp_email(omitted=0):
    from unittest.mock import patch
    from src.services.email_service import send_dark_pool_alert_email
    calls = []
    def fake(to, subject, html, text):
        calls.append({"html": html, "text": text})
        return True
    cand = {"symbol": "TSM", "premium": 5e6, "size": 25000, "price": 200.0, "exec_price": 200.0,
            "live_price": 201.0, "venue": "D", "executed_at": "2026-11-10T15:00:00Z", "side": None}
    with patch("src.services.email_service.send_email", fake):
        send_dark_pool_alert_email("u@example.com", [cand], omitted_count=omitted)
    return calls[0]


def test_the_email_says_the_symbols_are_ones_the_reader_follows():
    """AUD-DP-AUDIENCE (2026-11-10): each recipient now receives only symbols they follow."""
    out = _dp_email()
    for body in (out["html"], out["text"]):
        assert "your watched symbols" not in body
        assert "symbols you follow" in body


def test_owned_symbols_lookup_on_real_models():
    """Watchlist (direct and via list owner) plus open price alerts; fails closed."""
    import subprocess, sys
    probe = pathlib.Path(__file__).with_name("_owned_symbols_probe.py")
    r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "OWNED_SYMBOLS_PROBE_OK" in r.stdout, r.stdout + r.stderr


def test_each_recipient_is_sent_only_their_own_symbols():
    body = _dark_pool_body()
    assert "_owned = owned_symbols_by_user(session, list(recipients.keys()))" in body
    loop = body[body.index("for uid, user in recipients.items():"):]
    assert loop.index("if symbol.upper() not in _mine:") < loop.index("_dark_pool_alert_key(uid, symbol")


class _ScanSession:
    """Answers the one Stock lookup with whatever symbols are listed as tradeable US stocks."""
    def __init__(self, tradeable):
        self.tradeable = tradeable

    def execute(self, stmt):
        rows = [(100 + i, sym) for i, sym in enumerate(self.tradeable)]
        return type("R", (), {"all": lambda self_: rows})()


def test_followed_symbols_are_added_to_the_scan_most_followed_first_and_capped():
    base = [(1, "AAPL")]
    owned = {1: {"AAPL", "TSM", "MU"}, 2: {"TSM"}, 3: {"0700.HK", "ZZZ"}}
    scan, added = sched._dark_pool_scan_symbols(_ScanSession(["TSM", "MU", "ZZZ"]), base, owned)
    assert scan[0] == (1, "AAPL")                     # the shared list stays first
    assert [s for _id, s in scan[1:]] == ["TSM", "MU", "ZZZ"]   # 2 followers, then alphabetical
    assert added == 3


def test_the_scan_extension_is_capped():
    owned = {1: {f"S{i:03d}" for i in range(100)}}
    tradeable = sorted(owned[1])
    scan, added = sched._dark_pool_scan_symbols(_ScanSession(tradeable), [], owned)
    assert added == sched._DARK_POOL_OWNED_SCAN_CAP == len(scan)


def test_the_omitted_count_is_described_as_symbols_in_this_check():
    """omitted_count is extra SYMBOLS beyond the per-email cap in one run, not prints or a day."""
    out = _dp_email(omitted=3)
    for body in (out["html"], out["text"]):
        assert "+ 3 more symbols with a qualifying print in this check" in body
        assert "more prints today" not in body


def test_the_venue_is_not_called_a_dark_pool_venue():
    out = _dp_email()
    assert "dark pool venue" not in out["html"]
    assert "does not establish which dark pool or ATS executed it" in out["html"]


def test_a_print_with_no_execution_price_does_not_qualify():
    """AUD-DP-NOPRICE (2026-11-10): a provider premium with no price became alert_price 0.0 or
    carried the live price as the execution price."""
    class P:
        def __init__(self, price):
            self.price = price
    for bad in (None, 0, 0.0, -1.0, float("nan"), float("inf"), True, "200"):
        assert sched._dark_pool_print_priced(P(bad)) is False, bad
    assert sched._dark_pool_print_priced(P(200.25)) is True
    body = _dark_pool_body()
    i = body.index("qualifying = [")
    block = body[i:i + 2400]
    assert block.index("if not _dark_pool_print_priced(r):") < block.index("qualifying.append(")


def test_an_unidentified_or_unflagged_print_does_not_qualify():
    """AUD-DP-UNIDENTIFIED (2026-11-10): the alert uses the ledger's identity rule."""
    body = _dark_pool_body()
    i = body.index("qualifying = [")
    block = body[i:i + 2400]
    assert 'if not getattr(r, "tracking_id", None) or getattr(r, "canceled", None) is not False:' in block
    assert block.index("is not False:") < block.index("qualifying.append(")


# ── AUD-DP-SESSIONGATE / AUD-DP-LIVEAGE (2026-11-10), decision D11 ──────────────────────────

def test_emails_and_outcomes_are_gated_to_the_session_but_polling_is_not():
    body = _dark_pool_body()
    gate = body.index("if not _dark_pool_alerts_in_session():")
    assert body.index("_persist_dark_pool_prints(session, stock_id, symbol, rows)") < gate
    assert gate < body.index("                _record_dark_pool_alert_outcome(\n")
    assert gate < body.index("send_ok = send_dark_pool_alert_email(")


def test_the_session_check_follows_the_market_calendar_and_fails_open():
    from unittest.mock import patch
    import src.services.paper_trading_engine as pte
    with patch.object(pte, "_is_market_hours", lambda m: False):
        assert sched._dark_pool_alerts_in_session() is False
    with patch.object(pte, "_is_market_hours", lambda m: True):
        assert sched._dark_pool_alerts_in_session() is True
    def boom(m):
        raise RuntimeError("calendar down")
    with patch.object(pte, "_is_market_hours", boom):
        assert sched._dark_pool_alerts_in_session() is True


def test_a_live_price_from_an_earlier_session_is_not_used_as_live():
    body = _dark_pool_body()
    assert 'price = live.get("price") if live and not _live_row_from_earlier_session(live, False) else None' in body


def test_prints_are_persisted_only_for_a_fresh_provider_answer():
    """AUD-DP-JOB-OVERRUN (2026-11-11): re-writing a 15-minute cached answer every minute for
    80 symbols made the job overrun its one-minute slot 76 times in one session."""
    body = _dark_pool_body()
    i = body.index("_dp_fetch = _uw.fetch_dark_pool_prints(symbol)")
    block = body[i:i + 1800]
    assert "rows = _uw.usable_dark_pool_rows(_dp_fetch.rows)" in block
    assert "if not _dp_fetch.from_cache:\n                        _persist_dark_pool_prints(session, stock_id, symbol, rows)" in block
