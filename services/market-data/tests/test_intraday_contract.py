"""Intraday Stage C contract and research fill model on frozen bars (design revision 3).

Covers the review's acceptance lists for CDR-02, CDR-03 and CDR-R2-01, plus sessions, hashing
and the transition rule. Pure functions only; storage is tested in test_intraday_store*.py.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from common.horizons import UnsupportedMarket
from src.services.intraday import contract as c
from src.services.intraday import fills as f

UTC = timezone.utc
D = date(2026, 10, 28)                     # Wednesday, EDT: 13:30-20:00 UTC
EARLY = date(2026, 11, 27)                 # NYSE early close 13:00 ET = 18:00 UTC (EST)
T = c.session_times("US", D)


def at(h, m, d=D):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def bars(start, n, *, o=100.0, h=100.5, l=99.5, cl=100.0, skip=()):
    out = []
    for i in range(n):
        t = start + timedelta(minutes=5 * i)
        if t in skip:
            continue
        out.append(f.Bar(t, o, h, l, cl))
    return out


# ── Sessions ──────────────────────────────────────────────────────────────────────────────

def test_session_times_normal_and_early_close():
    assert (T.open, T.close) == (at(13, 30), at(20, 0))
    assert (T.last_entry_at, T.liquidation_at) == (at(19, 30), at(19, 50))
    e = c.session_times("US", EARLY)
    assert e.close == at(18, 0, EARLY)
    assert (e.last_entry_at, e.liquidation_at) == (at(17, 30, EARLY), at(17, 50, EARLY))


def test_hk_and_unknown_venues_are_refused_and_holidays_are_not_sessions():
    for m in ("HK", "hk", "LSE", "", None):
        with pytest.raises(UnsupportedMarket):
            c.session_times(m, D)
    assert c.session_times("US", date(2026, 11, 26)) is None          # Thanksgiving


def test_calendar_coverage_stop():
    with pytest.raises(c.CalendarCoverageExpired):
        c.session_times("US", date(2099, 1, 5))


def test_last_bar_close_is_clipped_to_an_early_close():
    e = c.session_times("US", EARLY)
    assert c.bar_close(at(17, 55, EARLY), "5m", e) == e.close
    assert c.readable_at(at(17, 55, EARLY), "5m", e) == e.close + timedelta(minutes=2)


# ── Canonical hashing ─────────────────────────────────────────────────────────────────────

def test_canonical_encoding_is_stable_and_refuses_non_finite():
    naive, aware = datetime(2026, 10, 28, 14, 0), datetime(2026, 10, 28, 10, 0, tzinfo=c.NY)
    assert c.canonical([naive]) == c.canonical([aware]) == b'["2026-10-28T14:00:00.000000Z"]'
    assert c.canonical([0.1, None, True, "é"]) == '[0.1,null,true,"é"]'.encode()
    for bad in (float("nan"), float("inf")):
        with pytest.raises(c.ContractViolation):
            c.sha([bad])


def test_each_hash_separates_what_it_covers():
    kw = dict(stock_id=1, symbol="aapl", timeframe="5m", bar_open=at(14, 0), open=1.0, high=2.0,
              low=0.5, close=1.5, adj_close=None, volume=100.0, session="REGULAR")
    r = c.raw_hash(**kw)
    assert r == c.raw_hash(**{**kw, "symbol": "AAPL"})
    assert r != c.raw_hash(**{**kw, "volume": 101.0})                   # volume-only revision
    assert r != c.raw_hash(**{**kw, "close": 1.5000001})                # sub-tolerance revision
    assert c.source_hash(r, "uw") != c.source_hash(r, "yfinance")       # source-only switch
    assert c.source_hash(r, None) == c.source_hash(r, "unknown")
    ih = dict(timeframe="5m", bar_open=at(14, 0), open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0,
              price_basis="raw", price_basis_reason="no_adj")
    assert c.input_hash(**ih) != c.input_hash(**{**ih, "price_basis": "adjusted"})   # basis change
    assert c.derived_hash("x", ["a", "b"]) != c.derived_hash("x", ["b", "a"])


def test_identity_changes_with_strategy_source_but_not_with_an_unrelated_deploy():
    base = dict(strategy_id="AI_INTRADAY", strategy_version="1", strategy_source_hash="s1",
                contract_version=c.CONTRACT_VERSION, config_hash=c.config_hash(c.Params()))
    assert c.identity_hash(**base) == c.identity_hash(**dict(base))
    assert c.identity_hash(**base) != c.identity_hash(**{**base, "strategy_source_hash": "s2"})
    assert c.config_hash(c.Params()) != c.config_hash(c.Params(entry_window_min=10))


# ── Transitions ───────────────────────────────────────────────────────────────────────────

def test_append_rule_first_transition_sequence_and_from_state():
    c.check_append(None, 1, None, "detected")
    for bad in [(None, 2, None, "detected"), (None, 1, None, "actionable")]:
        with pytest.raises(c.ContractViolation):
            c.check_append(*bad)
    c.check_append((1, "detected"), 2, "detected", "actionable")
    with pytest.raises(c.ContractViolation):
        c.check_append((2, "actionable"), 3, "detected", "expired_before_detection")   # stale from_state
    with pytest.raises(c.ContractViolation):
        c.check_append((2, "actionable"), 4, "actionable", "entered")                   # gap in seq
    with pytest.raises(c.ContractViolation):
        c.check_append((3, "exited"), 4, "exited", "reconciled")                        # illegal pair
    c.check_append((3, "unresolved_exit_at_close"), 4, "unresolved_exit_at_close", "reconciled")


def test_state_classes():
    assert "unresolved_exit_at_close" in c.SESSION_TERMINAL and "unresolved_exit_at_close" not in c.FINAL
    assert "reconciled" in c.FINAL and "reconciled" not in c.SESSION_TERMINAL


# ── Detection (CDR-03) ────────────────────────────────────────────────────────────────────

def detect(detected, trigger=at(14, 0), **kw):
    return c.decide_at_detection(trigger_bar_open=trigger, detected_at=detected, times=T,
                                 **{"direction": "bullish", "stop_breached": False, "inputs_ok": True, **kw})


def test_detection_before_at_and_after_expiry():
    # trigger 14:00-14:05, readable 14:07, expires 14:20 (exclusive)
    d = detect(at(14, 7))
    assert d.state == "actionable" and d.actionable_at == at(14, 7) and d.entry_expires_at == at(14, 20)
    assert detect(at(14, 20)).state == "expired_before_detection"
    assert detect(at(14, 21)).actionable_at is None
    # 14:17: the only open before 14:20 is 14:15 -- not after detection -> no opportunity
    assert detect(at(14, 17)).state == "no_fill_opportunity"
    with pytest.raises(c.ContractViolation):
        detect(at(14, 6))                                   # before readable


def test_detection_just_before_the_session_cutoff_and_with_no_open_left():
    # trigger 19:10-19:15, expires min(19:30, last_entry 19:30)=19:30; detected 19:17 -> 19:20 open ok
    assert detect(at(19, 17), trigger=at(19, 10)).state == "actionable"
    # trigger 19:20-19:25: expires 19:30; detected 19:27 -> no open in (19:27, 19:30)
    assert detect(at(19, 27), trigger=at(19, 20)).state == "no_fill_opportunity"


def test_cancelled_before_actionable_and_bearish_research_only():
    assert detect(at(14, 7), stop_breached=True).reason == "invalidated_price"
    assert detect(at(14, 7), inputs_ok=False).reason == "data_unavailable"
    d = detect(at(14, 7), direction="bearish")
    assert d.state == "research_only_unsupported_direction" and d.actionable_at is None


def test_setup_invariants():
    good = [dict(readable_at=at(14, 7), input_read_at=at(14, 7, ))]
    c.check_setup_invariants(detected_at=at(14, 8), actionable_at=at(14, 8), entry_expires_at=at(14, 20),
                             times=T, inputs=good)
    with pytest.raises(c.ContractViolation):
        c.check_setup_invariants(detected_at=at(14, 8), actionable_at=None, entry_expires_at=at(14, 20), times=T,
                                 inputs=[dict(readable_at=at(14, 7), input_read_at=at(14, 6))])


# ── Event timing (CDR-R2-01) ──────────────────────────────────────────────────────────────

def test_a_late_observed_cancellation_is_not_backdated_to_eligibility():
    # missing/revised bar eligible 14:07 but only read at 14:12, after the 14:10 entry open
    ev = c.EventTimes(eligible_at=at(14, 7), observed_at=at(14, 12), decided_at=at(14, 12, ), clock_basis="observed")
    eff = c.effective_time(ev)
    assert eff == at(14, 12)
    assert c.cancellation_vs_entry(eff, at(14, 10)) == "entered"


def test_an_earlier_observed_cancellation_still_defeats_a_later_entry():
    ev = c.EventTimes(at(14, 7), at(14, 8), at(14, 9), "observed")
    assert c.cancellation_vs_entry(c.effective_time(ev), at(14, 10)) == "cancelled"


def test_unknown_chronology_is_ambiguous_not_cancellation():
    assert c.effective_time(c.EventTimes(at(14, 7), at(14, 8), None, "observed")) is None
    assert c.cancellation_vs_entry(None, at(14, 10)) == "chronology_ambiguous"


def test_observed_times_must_be_ordered_and_replay_uses_a_labelled_logical_clock():
    with pytest.raises(c.ContractViolation):
        c.effective_time(c.EventTimes(at(14, 9), at(14, 8), at(14, 10), "observed"))
    with pytest.raises(c.ContractViolation):
        c.effective_time(c.EventTimes(at(14, 7), at(14, 9), at(14, 8), "observed"))
    rp = c.effective_time(c.EventTimes(at(14, 7), None, None, "replay_logical"), c.Params(replay_latency_s=90))
    assert rp == at(14, 7) + timedelta(seconds=90)


# ── Fills (CDR-02) ────────────────────────────────────────────────────────────────────────

P0 = c.Params(slippage_bps=0.0)


def sim(bs, *, actionable=at(14, 7), expires=at(14, 20), stop=99.0, target=102.0, params=P0, **kw):
    return f.simulate_long(entry_reference=100.0, stop=stop, target=target, actionable_at=actionable,
                           entry_expires_at=expires, times=T, bars=bs, params=params, **kw)


def test_detection_exactly_on_a_bar_open_uses_the_next_open():
    r = sim(bars(at(14, 0), 40), actionable=at(14, 10))
    assert r.entry_fill_at == at(14, 15)                     # strict: not the 14:10 open


def test_fill_at_the_expiry_boundary_is_not_allowed():
    # actionable 14:12, expires 14:15: the 14:15 open is not < expiry -> no opportunity
    assert sim(bars(at(14, 0), 40), actionable=at(14, 12), expires=at(14, 15)).state == "expired"


def test_entry_bar_double_touch_is_a_stop_and_intrabar_time_unknown():
    bs = bars(at(14, 0), 40)
    bs[2] = f.Bar(at(14, 10), 100.0, 103.0, 98.0, 100.0)
    r = sim(bs)
    assert (r.state, r.exit_reason, r.exit_at, r.exit_intrabar_time) == ("exited", "stop", at(14, 10), "unknown")
    assert r.exit_price == 99.0


def test_gap_through_the_stop_at_entry_rejects_the_bracket_without_moving_levels():
    bs = bars(at(14, 0), 40)
    bs[2] = f.Bar(at(14, 10), 98.5, 99.0, 98.0, 98.5)
    r = sim(bs)
    assert r.state == "rejected_invalid_bracket_at_fill" and r.exit_at is None


def test_later_gap_through_stop_fills_at_the_open():
    bs = bars(at(14, 0), 40)
    bs[4] = f.Bar(at(14, 20), 98.0, 98.2, 97.5, 98.0)
    r = sim(bs)
    assert (r.exit_reason, r.exit_at, r.exit_price, r.exit_intrabar_time) == ("stop", at(14, 20), 98.0, None)


def test_target_needs_a_trade_through():
    bs = bars(at(14, 0), 40)
    bs[3] = f.Bar(at(14, 15), 100.0, 102.0, 99.8, 101.0)     # touches, does not trade through
    bs[4] = f.Bar(at(14, 20), 101.0, 102.5, 100.5, 102.0)
    r = sim(bs)
    assert (r.exit_reason, r.exit_at, r.exit_price) == ("target", at(14, 20), 102.0)


def test_max_hold_is_a_scheduled_open_and_precedes_the_bar_s_own_stop():
    bs = bars(at(14, 0), 60)
    t_exit = at(16, 10)                                          # 14:10 + 120 min
    i = [b.open_at for b in bs].index(t_exit)
    bs[i] = f.Bar(t_exit, 100.0, 100.2, 98.0, 98.5)              # stop touched inside the exit bar
    r = sim(bs)
    assert (r.exit_reason, r.exit_at, r.exit_price) == ("max_hold", t_exit, 100.0)


def test_liquidation_uses_the_scheduled_deadline_open():
    bs = bars(at(13, 30), 78)
    r = sim(bs, actionable=at(19, 17), expires=at(19, 30))
    assert r.entry_fill_at == at(19, 20) and (r.exit_reason, r.exit_at) == ("liquidation", at(19, 50))


def test_late_liquidation_fills_at_the_next_observed_open_before_close():
    bs = bars(at(13, 30), 78, skip={at(19, 50)})
    r = sim(bs, actionable=at(19, 17), expires=at(19, 30))
    assert (r.exit_reason, r.exit_at) == ("liquidation", at(19, 55)) and r.unobserved_intervals == 1


def test_no_observation_before_close_is_unresolved_and_never_a_closing_fill():
    bs = bars(at(13, 30), 78, skip={at(19, 50), at(19, 55)})
    r = sim(bs, actionable=at(19, 17), expires=at(19, 30))
    assert r.state == "unresolved_exit_at_close"
    assert r.exit_price is None and r.r_multiple is None and r.mark == 100.0 and r.mark_at == at(19, 50)


def test_competing_timed_and_stop_exits_follow_one_precedence():
    # stop gap at the open of the same bar the time exit is scheduled on: the scheduled exit wins
    bs = bars(at(14, 0), 60)
    i = [b.open_at for b in bs].index(at(16, 10))
    bs[i] = f.Bar(at(16, 10), 98.0, 98.1, 97.0, 97.5)
    assert sim(bs).exit_reason == "max_hold"


def test_missing_entry_bar_is_data_unavailable_not_a_later_fill():
    r = sim(bars(at(14, 0), 40, skip={at(14, 10)}))
    assert (r.state, r.reason) == ("cancelled", "data_unavailable")
    assert r.evidence_eligible_at == at(14, 17)


def test_cancellation_timing_against_entry():
    bs = bars(at(14, 0), 40)
    assert sim(bs, cancellation_effective=at(14, 9)).state == "cancelled"
    late = sim(bs, cancellation_effective=at(14, 12))
    assert late.entry_fill_at == at(14, 10) and late.state == "exited" and late.exit_reason == "max_hold"
    assert sim(bs, cancellation_known=False).state == "chronology_ambiguous"


def test_costs_and_slippage_reduce_r():
    bs = bars(at(14, 0), 40)
    bs[4] = f.Bar(at(14, 20), 101.0, 102.5, 100.5, 102.0)
    free = sim(bs)
    costly = sim(bs, params=c.Params(slippage_bps=5.0, cost_bps=1.0, fee_per_share=0.01))
    assert costly.r_multiple < free.r_multiple
    assert costly.entry_fill_price == pytest.approx(100.05)


def test_research_labels():
    r = sim(bars(at(14, 0), 40))
    assert r.fill_basis == "next_bar_open_research" and r.execution_grade is False


# ── Forecast outcomes ─────────────────────────────────────────────────────────────────────

def test_forecast_resolved_truncated_and_missing_terminal_bar():
    bs = bars(at(13, 30), 78)
    out = {o.window_min: o for o in f.forecast_outcomes(trigger_bar_open=at(14, 0), trigger_close=100.0,
                                                        times=T, bars=bs)}
    assert out[30].status == "resolved" and out[30].end_bar_open == at(14, 30)
    late = {o.window_min: o for o in f.forecast_outcomes(trigger_bar_open=at(19, 0), trigger_close=100.0,
                                                         times=T, bars=bs)}
    assert late[30].status == "resolved"
    assert late[120].status == "truncated_session_end" and late[120].actual_min == 55
    gap = {o.window_min: o for o in f.forecast_outcomes(trigger_bar_open=at(14, 0), trigger_close=100.0,
                                                        times=T, bars=bars(at(13, 30), 78, skip={at(15, 0)}))}
    assert gap[60].status == "missing_terminal_bar" and gap[60].ret is None
    assert all(o.entry_basis == "trigger_close_descriptive" for o in out.values())
