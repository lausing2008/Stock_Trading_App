"""Email must distinguish research triggers, execution and follow-up expectations."""
from datetime import datetime, timezone
from src.services.little_rzy import Config
from src.services.rzy_email import render_event, render_event_html, event_subject, chart_url

CFG = Config()
CLOSE = '2026-10-20T19:05:00+00:00'
RENDERED = datetime(2026, 10, 20, 19, 7, 30, tzinfo=timezone.utc)
CHECKED = datetime(2026, 10, 20, 19, 7, 5, tzinfo=timezone.utc)
BLOCKERS = ('provider_delay_unverified', 'borrow_availability_not_stored', 'execution_basis_unverified')
TIMES = dict(evaluated_at=CHECKED, rendered_at=RENDERED)


def sample(**changes):
    # Shape follows Engine.emit()'s payload for a TRIGGERED event.
    base = dict(event_type='TRIGGERED', position_action='OPEN_SHORT', direction='short', symbol='ANF', timeframe='5m',
                signal_time=CLOSE, data_as_of=CLOSE, planned_entry=161.26, stop=161.6751, primary_target=157.2525,
                runner_target=None, reward_risk=9.6553, trendline_value=161.40, atr=0.52, middle_band=162.0,
                upper_band=163.1, lower_band=160.9, D=6.1, sequence_number=1, sequence_confidence='known',
                anchors=[dict(price=165.0), dict(price=158.9), dict(price=162.3)], provider='yfinance', delay='unknown',
                price_basis='raw', data_freshness='historical_completed', config_hash=CFG.hash, strategy_version='rzy-1',
                structure_id='st1', generation=2, occurrence_id='occ123', event_id='occ123:g2', paper_state='PENDING_ENTRY',
                passed_rules=['confirmed_structure', 'line_touch', 'directional_close', 'previous_bar_break', 'planned_reward_risk'],
                failed_rules=[])
    base.update(changes)
    return base


def test_subject_format_for_each_event_type():
    assert event_subject(sample()) == 'RZY research | ANF | bearish trigger | 5m'
    assert event_subject(sample(position_action='OPEN_LONG', direction='long')) == 'RZY research | ANF | bullish trigger | 5m'
    assert event_subject(sample(event_type='REVERSAL_WATCH', position_action='WATCH_ONLY')) == 'RZY research | ANF | reversal watch | 5m'
    assert event_subject(sample(event_type='EXHAUSTION_WATCH')) == 'RZY research | ANF | exhaustion watch | 5m'
    assert event_subject(sample(event_type='EXIT_PENDING')) == 'RZY research | ANF | paper exit pending | 5m'
    assert event_subject(sample(event_type='PAPER_EXIT', paper_state='PARTIAL')) == 'RZY research | ANF | partial paper exit | 5m'
    assert event_subject(sample(event_type='PAPER_EXIT', paper_state='CLOSED')) == 'RZY research | ANF | paper position closed | 5m'
    assert event_subject(sample(event_type='ENTRY_CANCELLED')) == 'RZY research | ANF | paper entry cancelled | 5m'


def test_blocked_bearish_trigger_status_panel_comes_first_with_freshness():
    text = render_event(sample(), BLOCKERS, config=CFG, **TIMES)
    status = text.split('\n\nWhy this triggered')[0]
    assert 'Status & freshness\nResearch trigger — paper execution blocked' in status
    assert 'Candle close: Oct 20, 2026 · 03:05:00 PM EDT (19:05:00 UTC)' in status
    assert 'Freshness check before queueing: Oct 20, 2026 · 03:07:05 PM EDT' in status
    assert 'Email rendered (not inbox arrival): Oct 20, 2026 · 03:07:30 PM EDT' in status
    assert 'Signal age when rendered: 2 min 30 s.' in status
    # Readable meaning in the status panel; raw ids only in the technical footer.
    assert 'Data verification: execution unverified — provider delay is not verified' in status
    assert 'provider_delay_unverified' not in status
    # 5m: expiry = close + 5 + 5 min = 19:15 UTC; enqueue window +3 named as such.
    assert 'Notification expiry: Oct 20, 2026 · 03:15:00 PM EDT' in status and 'timeframe + 3 min' in status
    assert 'not how long the setup stays tradable' in status
    assert 'snapshot and cannot update itself' in status
    assert 'Follow-up emails are not guaranteed' in status
    assert 'Paper execution unavailable' in status and 'do not expect a corresponding trade-exit email' in status
    assert 'Research only' in status and 'No broker order' in status
    assert 'inbox arrival' not in text.replace('not inbox arrival', '')


def test_trigger_reasons_are_measured_and_levels_labelled():
    text = render_event(sample(), BLOCKERS, config=CFG, **TIMES)
    assert 'touched the A-C trendline at 161.4000; tolerance ±0.0520 (touch 0.1 × ATR 0.5200)' in text
    assert 'closed at 161.2600, below the trendline (161.4000)' in text
    assert "the prior-candle-break rule (beyond the prior candle's low) passed, but that prior-candle low is not recorded" in text
    assert "above its open (open not recorded" not in text and "the directional-close rule (below its open) passed" in text
    assert 'Planned reward:risk 9.66:1 before costs, at or above the configured minimum (1.5:1)' in text
    assert 'anchor prices 165.0000, 158.9000, 162.3000' in text
    assert 'Quote currency: currency not recorded.' in text
    assert 'Entry reference (trigger close, not a fill): 161.2600' in text and 'Structural stop: 161.6751' in text
    assert 'Risk distance: 0.4151 per share (0.26% of entry); target reward distance: 4.0075' in text
    assert '9.66:1, gross before costs' in text and 'not a win probability' in text
    assert 'a high reward:risk is not a high probability' in text and 'does not cap losses' in text
    assert 'fill range' not in text.lower() and 'acceptable range' not in text.lower()
    assert 'Original setup reference levels' not in text


def test_bullish_trigger_and_unknown_rule_and_mismatched_config():
    event = sample(position_action='OPEN_LONG', direction='long', passed_rules=['previous_bar_break', 'planned_reward_risk', 'new_rule_x'], currency='USD')
    text = render_event(event, (), config=Config(min_rr=2.0), **TIMES)   # different hash -> minimum not shown
    assert 'Bullish setup triggered' in text
    assert "beyond the prior candle's high) passed" in text
    assert 'configured minimum (not available)' in text
    assert 'new_rule_x (unrecognised rule identifier, shown raw)' in text
    assert 'Quote currency: USD.' in text
    assert 'Research trigger — paper entry pending checks, not a fill' in text and 'not a confirmed fill' in text
    assert 'no execution-grade verification is recorded' in text


def test_missing_measurements_are_explicit():
    event = sample(stop=None, reward_risk=None, trendline_value=None, atr=None, occurrence_id=None)
    text = render_event(event, BLOCKERS, rendered_at=RENDERED)
    assert 'Structural stop: not available' in text and 'Risk distance: not available.' in text
    assert 'Planned reward : risk: not available' in text
    assert 'Freshness check before queueing: not available.' in text
    assert 'trendline at not available; tolerance not available' in text
    assert 'Occurrence: not available' in text
    bad_time = render_event(sample(signal_time='garbage'), BLOCKERS, **TIMES)
    assert 'Candle close: not available' in bad_time and 'Signal age when rendered: not available' in bad_time
    assert 'Notification expiry: not available.' in bad_time


def test_watch_shows_watched_level_and_missing_confirmation_not_entry_rows():
    rev = render_event(sample(event_type='REVERSAL_WATCH', position_action='WATCH_ONLY', direction='long', passed_rules=[],
                              reason='midline_cross_after_completed_or_exhausted_invalidated_structure'), BLOCKERS, config=CFG, **TIMES)
    assert 'Research watch — not an entry trigger' in rev and 'not an entry trigger' in rev
    assert 'Watched level: Bollinger middle band 162.0000' in rev and 'possible new direction: long' in rev
    assert 'Still missing:' in rev and 'at least 1.5:1' in rev
    assert 'Entry reference' not in rev and 'Structural stop' not in rev
    exh = render_event(sample(event_type='EXHAUSTION_WATCH', position_action='WATCH_ONLY', direction='long', sequence_number=3,
                              reason='research_heuristic_sequence_outer_band_smaller_D'), BLOCKERS, **TIMES)
    assert 'Watched level: upper Bollinger band 163.1000; measured move D 6.1000; sequence 3' in exh
    assert 'Entry reference' not in exh
    odd = render_event(sample(event_type='EXHAUSTION_WATCH', reason='mystery'), (), **TIMES)
    assert 'mystery (unrecognised reason identifier, shown raw)' in odd


def test_exit_leads_with_exit_and_keeps_setup_secondary():
    event = sample(event_type='PAPER_EXIT', position_action='COVER_SHORT', exit_price=157.1, exit_quantity=1.0, actual_paper_fill=161.2,
                   net_pnl_per_unit=4.05, paper_state='PARTIAL', reason='primary_partial')
    text = render_event(event, BLOCKERS, **TIMES)
    assert '157.1000' in text and 'paper position remains' in text
    assert text.index('\nExit\n') < text.index('Original setup levels') and 'Original setup reference levels' in text
    assert 'Exit reason: the primary target was reached; partial exit.' in text
    assert 'Paper entry fill: 161.2000; net P&L per unit so far: 4.0500' in text
    event['paper_state'] = 'CLOSED'
    assert 'paper position closed' in event_subject(event) and 'The paper position is closed.' in render_event(event, **TIMES)
    pending = render_event(sample(event_type='EXIT_PENDING', position_action='COVER_SHORT', paper_state='OPEN'), **TIMES)
    assert 'exit fill is not yet confirmed' in pending and 'Planned exit: next bar open' in pending
    assert 'Paper entry fill: not recorded in this event' in pending


def test_cancellation_lists_readable_reasons():
    text = render_event(sample(event_type='ENTRY_CANCELLED', failed_rules=['data_basis_or_freshness_unverified', 'liquidity_requirement', 'zzz']), BLOCKERS, **TIMES)
    assert 'No paper position was opened' in text and 'Why the paper entry was cancelled' in text
    assert 'completed volume was below the configured minimum' in text
    assert 'not verified as execution-grade data' in text
    assert 'zzz (unrecognised identifier, shown raw)' in text


def test_chart_link_format_and_current_validity_note():
    assert chart_url(sample()) == 'https://lausing.com/stock/ANF?tf=5m&rzy_event=occ123'
    assert chart_url(sample(symbol='BRK.B', occurrence_id=None)) == 'https://lausing.com/stock/BRK.B?tf=5m'
    text = render_event(sample(), BLOCKERS, **TIMES)
    assert 'Open the stock chart: https://lausing.com/stock/ANF?tf=5m&rzy_event=occ123' in text
    assert 'CURRENT state, which may differ from this archived email' in text
    tech = text.split('Technical details\n')[1]
    assert 'Occurrence: occ123' in tech and 'generation: 2' in tech and CFG.hash in tech


def test_html_escapes_and_matches_text_content():
    event = sample(symbol='<ANF>', provider='<script>alert(1)</script>', occurrence_id='a"b')
    html = render_event_html(event, BLOCKERS, **TIMES)
    assert '<script>' not in html and '&lt;script&gt;' in html and '&lt;ANF&gt;' in html
    assert 'href="https://lausing.com/stock/%3CANF%3E?tf=5m&amp;rzy_event=a%22b"' in html
    assert 'Research trigger — paper execution blocked' in html and 'Follow-up emails are not guaranteed' in html
    assert 'do not expect a corresponding trade-exit email' in html and 'Signal age when rendered: 2 min 30 s.' in html
    assert '<pre>' not in html
    # Every plain-text line (other than the link) appears escaped in the HTML.
    text = render_event(sample(), BLOCKERS, **TIMES)
    html = render_event_html(sample(), BLOCKERS, **TIMES)
    from html import escape
    for line in text.splitlines()[2:]:
        if line and not line.startswith('Open the stock chart'):
            assert escape(line) in html, line


def test_the_engine_standing_blocker_ids_are_readable_too():
    text = render_event(sample(), ('data_basis_or_freshness_unverified', 'shortability_or_borrow_cost_unverified'), config=CFG, **TIMES)
    assert 'unrecognised identifier' not in text.split('Technical details')[0]
    assert 'the price data basis and freshness are not verified for execution' in text



# ── Morning watchlist digest ────────────────────────────────────────────────────────────────
from datetime import date
from src.services.rzy_email import render_digest


def _cand(symbol, direction, tf, entry, stop, target, rr):
    return dict(symbol=symbol, direction=direction, timeframe=tf, entry_reference=entry, provisional_stop=stop,
                target=target, planned_reward_risk=rr, data_as_of='2026-10-21T20:00:00+00:00', min_reward_risk=1.5)


def test_digest_states_setups_with_per_share_risk_and_explains_timeframes_once():
    report = dict(candidates=[_cand('ANET', 'long', '15m', 184.35, 181.0234, 197.8865, 4.07),
                              _cand('NOK', 'long', '5m', 9.255, 9.1917, 9.4093, 2.44),
                              _cand('ARMK', 'short', '15m', 55.71, 56.0765, 54.613, 2.99)],
                  total_candidates=14, exclusions=[])
    text, html = render_digest(report, date(2026, 10, 22))
    assert text.startswith('LITTLE RZY · WATCHLIST DIGEST · Thu Oct 22\n3 setups to watch (showing 3 of 14)')
    assert 'ANET · Long (buy) · 15-minute candles' in text
    assert 'Entry $184.35 · Stop $181.02 (risk $3.33/share) · Target $197.89 (reward $13.54/share) · Reward:risk 4.07' in text
    assert 'ARMK · Short (sell short) · 15-minute candles' in text
    assert 'SETUPS (charts as of Oct 21, 4:00 PM ET)' in text
    assert 'each 5m candle is 5 minutes of trading' in text and text.count('each 5m candle') == 1
    assert 'still at least 1.5' in text and text.count('Wait for') == 0           # instructions once, not per row
    assert 'https://lausing.com/stock/NOK?tf=5m' in html and 'How to use this email' in html


def test_digest_counts_routine_exclusions_and_lists_only_the_ones_worth_reading():
    ex = ([dict(symbol=f'S{i}', timeframe='5m', reason='no_armed_structure') for i in range(40)]
          + [dict(symbol='AMADY', timeframe='15m', reason='stale_15m_aggregate'),
             dict(symbol='ADBE', timeframe='1h', reason='insufficient_planned_reward_risk'),
             dict(symbol='ZZZ', timeframe='5m', reason='data_rejected: boom')])
    text, _ = render_digest(dict(candidates=[], total_candidates=0, exclusions=ex), date(2026, 10, 22))
    assert 'No setups today — do not force a trade.' in text
    assert 'NOT SHOWN: 43 watchlist charts' in text
    assert '  40 — no active setup on that chart\n' in text and 'S0 5m' not in text
    assert '1 — price data not current: AMADY 15m' in text
    assert '1 — setup found, but planned reward:risk below the minimum: ADBE 1h' in text
    assert '1 — price data rejected: ZZZ 5m' in text


def test_simulated_fill_is_distinct_from_trigger_and_broker_execution():
    e=sample(event_type='PAPER_FILL',actual_paper_fill=161.20,paper_fill_time=CLOSE,paper_state='OPEN')
    text=render_event(e,(),config=CFG,**TIMES)
    assert 'simulated entry fill' in event_subject(e)
    assert 'Simulated entry filled — no broker order' in text
    assert '161.2000' in text and 'This is not a broker fill' in text
    assert 'Actual-fill reward:risk' in text
    assert 'not an entry trigger or an instruction to reverse' not in text
    html=render_event_html(e,(),config=CFG,**TIMES)
    assert 'Simulated entry filled' in html


def test_structure_terminal_messages_do_not_claim_a_trade_exit():
    for kind in ('INVALIDATED','EXPIRED'):
        e=sample(event_type=kind)
        text=render_event(e,(),config=CFG,**TIMES)
        assert 'not a trade exit' in text
        assert 'does not confirm a fill or close a position you hold' in text
