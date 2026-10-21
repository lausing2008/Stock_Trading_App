"""Email must distinguish research triggers, execution and follow-up expectations."""
from src.services.rzy_email import render_event, render_event_html, event_subject


def sample(**changes):
    return dict(dict(event_type='TRIGGERED',position_action='OPEN_SHORT',symbol='ANF',timeframe='5m',
                     signal_time='2026-10-20T19:05:00+00:00',planned_entry=161.26,stop=161.6751,
                     primary_target=157.2525,reward_risk=9.6553,sequence_number=1,sequence_confidence='known'),**changes)


def test_blocked_trigger_explains_levels_and_no_exit_followup():
    text=render_event(sample(),('provider_delay_unverified',))
    assert 'Bearish setup triggered' in event_subject(sample())
    for expected in ('9.66:1','0.4151','4.0075','03:05 PM EDT','not a win probability',
                     'do not expect a corresponding trade-exit email','provider_delay_unverified'):
        assert expected in text
    assert 'Paper entry is pending' not in text


def test_unblocked_trigger_is_not_a_fill_or_guaranteed_exit():
    text=render_event(sample())
    assert 'not a confirmed fill' in text and 'Delivery is not guaranteed' in text


def test_watch_and_cancellation_are_not_entry_instructions():
    watch=render_event(sample(event_type='REVERSAL_WATCH',position_action='WATCH_ONLY'))
    assert 'not an entry trigger' in watch
    cancelled=render_event(sample(event_type='ENTRY_CANCELLED'))
    assert 'No paper position was opened' in cancelled


def test_exit_uses_actual_exit_and_distinguishes_partial_from_closed():
    event=sample(event_type='PAPER_EXIT',exit_price=157.1,paper_state='PARTIAL',reason='primary_partial')
    assert 'Partial paper exit' in event_subject(event)
    assert '157.1000' in render_event(event) and 'paper position remains' in render_event(event)
    event['paper_state']='CLOSED'
    assert 'Paper position closed' in event_subject(event)
    assert 'Original setup reference levels' in render_event(event)
    pending=render_event(sample(event_type='EXIT_PENDING'))
    assert 'exit fill is not yet confirmed' in pending


def test_html_escapes_provider_and_symbol_and_preserves_key_status():
    event=sample(symbol='<ANF>',provider='<script>alert(1)</script>')
    html=render_event_html(event,('provider_delay_unverified',))
    assert '<script>' not in html and '&lt;script&gt;' in html and '&lt;ANF&gt;' in html
    assert 'do not expect a corresponding trade-exit email' in html
    assert '<pre>' not in html
