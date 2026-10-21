"""Human-readable research alerts; presentation only, never trading decisions."""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo


def event_label(event):
    kind = event['event_type']
    if kind == 'TRIGGERED':
        return 'Bearish setup triggered' if event.get('position_action') == 'OPEN_SHORT' else 'Bullish setup triggered'
    return {'REVERSAL_WATCH': 'Possible reversal — watch only',
            'EXHAUSTION_WATCH': 'Trend exhaustion — watch only',
            'EXIT_PENDING': 'Paper exit pending',
            'PAPER_EXIT': 'Partial paper exit recorded' if event.get('paper_state') != 'CLOSED' else 'Paper position closed',
            'ENTRY_CANCELLED': 'Paper entry cancelled'}.get(kind, 'Pattern update — watch only')


def event_subject(event):
    return f"RZY research · {event_label(event)} · {event['symbol']} · {event['timeframe']}"


def sections(event, paper_blockers=()):
    def number(key):
        value = event.get(key)
        return 'not available' if value is None else f'{value:.4f}'

    stamp = event['signal_time']
    try:
        dt = datetime.fromisoformat(stamp)
        if dt.tzinfo:
            stamp = dt.astimezone(ZoneInfo('America/New_York')).strftime('%b %d, %Y · %I:%M %p %Z') + f' ({stamp})'
    except (ValueError, TypeError):
        pass
    kind = event['event_type']
    if kind == 'TRIGGERED' and paper_blockers:
        status = ('Paper execution unavailable. This setup will not open a paper position; '
                  'do not expect a corresponding trade-exit email. Later pattern watches may still arrive.')
    elif kind == 'TRIGGERED':
        status = ('Paper entry is pending the next tradable bar and fill checks; it is not a confirmed fill. '
                  'If filled, paper exit events can generate emails while your subscription and data remain eligible. '
                  'Delivery is not guaranteed.')
    elif kind == 'EXIT_PENDING':
        status = 'The paper strategy requests an exit at the next bar open. The protective stop remains active; an exit fill is not yet confirmed.'
    elif kind == 'PAPER_EXIT':
        status = (f"Paper exit price: {number('exit_price')}. Reason: {event.get('reason', 'unknown')}. "
                  + ('The paper position is closed.' if event.get('paper_state') == 'CLOSED' else 'This is a partial exit; a paper position remains.'))
    elif kind == 'ENTRY_CANCELLED':
        status = 'No paper position was opened; no trade-exit email is expected. Reason: ' + str(event.get('rejection_reason') or 'see technical details')
    else:
        status = 'Watch only: this is not an entry trigger or an instruction to reverse a position. No trade-exit email is promised by this watch.'
    levels = [f"Entry reference: {number('planned_entry')} (not a fill)",
              f"Protective stop: {number('stop')}", f"Primary target: {number('primary_target')}"]
    rr = event.get('reward_risk')
    levels.append('Planned reward : risk: ' + (f'{rr:.2f}:1, before costs' if rr is not None else 'not available'))
    if kind == 'TRIGGERED':
        entry, stop, target = (event.get(k) for k in ('planned_entry', 'stop', 'primary_target'))
        if all(v is not None for v in (entry, stop, target)):
            levels.append(f'Planned risk per share: {abs(stop-entry):.4f}; target reward per share: {abs(target-entry):.4f} (stock quote currency).')
    levels.append('Reward : risk compares the distance to the target with the distance to the stop. It is not a win probability; costs and actual fills change it.')
    if kind != 'TRIGGERED':
        levels.insert(0, 'Original setup reference levels — not a new entry or current trailing stop.')
    seq = str(event.get('sequence_number')) if event.get('sequence_confidence') == 'known' else 'unknown (history begins mid-trend)'
    return [
        ('Status & follow-up', [status, 'Research only. No broker order is placed, and this alert does not manage your actual holdings. Do not use email as your stop-loss mechanism.']),
        ('Setup levels', levels),
        ('How to interpret this', ['A bearish setup looks for a falling price; a bullish setup looks for a rising price. These are conditional pattern signals, not instructions to trade.',
                                   'RZY is an independent rule-based research signal, not the AI signal. Historical evaluation has not demonstrated a trading edge.']),
        ('Technical details', [f'Completed candle: {stamp}; timeframe: {event["timeframe"]}.',
                               f'Sequence: {seq}. Event: {kind}. Action code: {event.get("position_action", "WATCH_ONLY")}.',
                               f'Provider: {event.get("provider", "unknown")}; delay: {event.get("delay", "unknown")}.',
                               'Paper blockers: ' + (', '.join(paper_blockers) or 'none reported; fill checks still apply'),
                               'Passed: ' + (', '.join(event.get('passed_rules') or []) or 'none recorded'),
                               'Failed: ' + (', '.join(event.get('failed_rules') or []) or 'none recorded'),
                               f'Structure: {event.get("structure_id", "unknown")}. Event ID: {event.get("event_id", "unknown")}.']),
        ('Preferences', ['Manage these independent email preferences in Settings → Short-term trading & Little RZY.'])]


def render_event(event, paper_blockers=()):
    return event_subject(event) + '\n\n' + '\n\n'.join(title + '\n' + '\n'.join(lines) for title, lines in sections(event, paper_blockers)) + '\n'


def render_event_html(event, paper_blockers=()):
    cards = []
    for title, lines in sections(event, paper_blockers):
        cards.append('<div style="padding:16px 20px;border-top:1px solid #e2e8f0">'
                     f'<h2 style="font-size:16px;margin:0 0 10px">{escape(title)}</h2>'
                     + ''.join(f'<p style="margin:8px 0;overflow-wrap:anywhere">{escape(line)}</p>' for line in lines) + '</div>')
    return ('<div style="max-width:640px;margin:auto;font:15px/1.5 Arial,sans-serif;color:#172033;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px">'
            '<div style="padding:20px;background:#eff6ff"><p style="margin:0;font-size:12px">LITTLE RZY · RESEARCH ALERT</p>'
            f'<h1 style="font-size:22px;margin:8px 0">{escape(event["symbol"])} · {escape(event["timeframe"])}</h1>'
            f'<p style="margin:0">{escape(event_label(event))}</p></div>' + ''.join(cards) + '</div>')
