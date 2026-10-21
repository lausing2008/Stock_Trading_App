"""Human-readable research alerts; presentation only, never trading decisions.

Every figure shown comes from the event payload (or the explicitly passed config/times). A value
the payload does not carry is printed as "not available" / "not recorded", never estimated.
"""
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from .rzy_data import MINUTES

NY = ZoneInfo('America/New_York')
CHART_BASE_URL = 'https://lausing.com'
NA = 'not available'
# The scanner only queues an event within timeframe + 3 min of candle close; the outbox discards
# an unsent copy at timeframe + 5 min. Both are notification limits, not trading validity.
ENQUEUE_GRACE_MIN = 3
DELIVERY_GRACE_MIN = 5
WATCH_TYPES = ('REVERSAL_WATCH', 'EXHAUSTION_WATCH')
EXIT_TYPES = ('EXIT_PENDING', 'PAPER_EXIT')

# Readable descriptions of identifiers emitted by little_rzy.py / rzy_data.py.
CANCEL_REASONS = {
    'trend_epoch_changed': 'the trend epoch changed between the trigger and the next open',
    'data_basis_or_freshness_unverified': 'the next-open bar is not verified as execution-grade data (execution_eligible is not true)',
    'liquidity_requirement': "the trigger candle's completed volume was below the configured minimum",
    'shortability_or_borrow_cost_unverified': 'shortability or borrow cost is not verified for a short entry',
    'borrow_cost_invalid': 'the supplied borrow rate is not a finite non-negative number',
    'actual_fill_reward_risk_or_gap': 'at the modelled next-open fill (after spread/slippage/fees) the reward:risk fell below the minimum or the open gapped past the stop/target',
    'NO_CHASE_FILTER': 'the trigger close was outside the Bollinger band (no-chase filter)',
}
PAPER_BLOCKERS = {
    'provider_delay_unverified': 'provider delay is not verified',
    'borrow_availability_not_stored': 'borrow availability is not stored',
    'execution_basis_unverified': 'the price basis is not verified for execution',
    # The engine's own standing failure ids (little_rzy.py) for the same two facts.
    'data_basis_or_freshness_unverified': 'the price data basis and freshness are not verified for execution',
    'shortability_or_borrow_cost_unverified': 'shortability or borrow cost is not verified for a short entry',
}
EXIT_REASONS = {
    'structural_invalidation_next_open': 'a candle closed back through the structure trendline; exited at the next open',
    'protective_stop_conservative_order': 'the protective stop was reached (stop counted first when the bar order is unknown)',
    'primary_partial': 'the primary target was reached; partial exit',
    'partial_stop_conservative_order': 'after the partial exit, the moved-up stop was reached',
    'runner_target': 'the runner target was reached',
    'primary_target': 'the primary target was reached',
}
WATCH_REASONS = {
    'midline_cross_after_completed_or_exhausted_invalidated_structure':
        'after a completed or exhausted structure was invalidated, a candle closed across the Bollinger middle band',
    'research_heuristic_sequence_outer_band_smaller_D':
        'a late-sequence structure with a smaller measured move (D) than the previous one closed back inside the outer Bollinger band',
}


def _bearish(event):
    if event.get('position_action') in ('OPEN_SHORT', 'COVER_SHORT'):
        return True
    return event.get('direction') == 'short'


def event_label(event):
    kind = event['event_type']
    if kind == 'TRIGGERED':
        return 'Bearish setup triggered' if event.get('position_action') == 'OPEN_SHORT' else 'Bullish setup triggered'
    return {'REVERSAL_WATCH': 'Possible reversal — watch only',
            'EXHAUSTION_WATCH': 'Trend exhaustion — watch only',
            'EXIT_PENDING': 'Paper exit pending',
            'PAPER_EXIT': 'Partial paper exit recorded' if event.get('paper_state') != 'CLOSED' else 'Paper position closed',
            'ENTRY_CANCELLED': 'Paper entry cancelled'}.get(kind, 'Pattern update — watch only')


def _short_label(event):
    kind = event['event_type']
    if kind == 'TRIGGERED':
        return 'bearish trigger' if event.get('position_action') == 'OPEN_SHORT' else 'bullish trigger'
    return {'REVERSAL_WATCH': 'reversal watch', 'EXHAUSTION_WATCH': 'exhaustion watch',
            'EXIT_PENDING': 'paper exit pending',
            'PAPER_EXIT': 'paper position closed' if event.get('paper_state') == 'CLOSED' else 'partial paper exit',
            'ENTRY_CANCELLED': 'paper entry cancelled'}.get(kind, 'pattern update')


def event_subject(event):
    return f"RZY research | {event['symbol']} | {_short_label(event)} | {event['timeframe']}"


def _parse(value):
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _stamp(dt):
    if dt is None:
        return NA
    return dt.astimezone(NY).strftime('%b %d, %Y · %I:%M:%S %p %Z') + f" ({dt.astimezone(timezone.utc).strftime('%H:%M:%S')} UTC)"


def _age(later, earlier):
    if later is None or earlier is None:
        return NA
    seconds = int((later - earlier).total_seconds())
    if seconds < 0:
        return NA + ' (render time precedes candle close)'
    return f'{seconds // 60} min {seconds % 60:02d} s'


def _num(value):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return NA
    return f'{value:.4f}'


def _status_heading(kind, blocked, event):
    if kind == 'TRIGGERED':
        return 'Research trigger — paper execution blocked' if blocked else 'Research trigger — paper entry pending checks, not a fill'
    if kind in WATCH_TYPES:
        return 'Research watch — not an entry trigger'
    if kind == 'EXIT_PENDING':
        return 'Paper exit pending — exit fill not yet confirmed'
    if kind == 'PAPER_EXIT':
        return 'Paper position closed' if event.get('paper_state') == 'CLOSED' else 'Partial paper exit recorded'
    if kind == 'ENTRY_CANCELLED':
        return 'Paper entry cancelled — no paper position'
    return 'Research update — watch only'


def _status(event, blockers, evaluated_at, rendered_at):
    kind = event['event_type']
    signal = _parse(event.get('signal_time'))
    tf_minutes = MINUTES.get(event.get('timeframe'))
    lines = [_status_heading(kind, bool(blockers), event)]
    if kind == 'TRIGGERED' and blockers:
        lines.append('Paper execution unavailable. This setup will not open a paper position; '
                     'do not expect a corresponding trade-exit email.')
    elif kind == 'TRIGGERED':
        lines.append('Paper entry is pending the next tradable bar and fill checks; it is not a confirmed fill. '
                     'Delivery is not guaranteed.')
    elif kind == 'EXIT_PENDING':
        lines.append('The paper strategy requests an exit at the next bar open. The protective stop remains active; an exit fill is not yet confirmed.')
    elif kind == 'PAPER_EXIT':
        lines.append('The paper position is closed.' if event.get('paper_state') == 'CLOSED'
                     else 'This is a partial exit; a paper position remains.')
    elif kind == 'ENTRY_CANCELLED':
        lines.append('No paper position was opened; no trade-exit email is expected.')
    else:
        lines.append('Watch only: this is not an entry trigger or an instruction to reverse a position.')
    lines += [f'Candle close: {_stamp(signal)}; timeframe: {event.get("timeframe", NA)}.',
              f'Freshness check before queueing: {_stamp(evaluated_at)}.',
              f'Email rendered (not inbox arrival): {_stamp(rendered_at)}.',
              f'Signal age when rendered: {_age(rendered_at, signal)}.']
    if blockers:
        readable = '; '.join(PAPER_BLOCKERS.get(b, f'{b} (unrecognised identifier, shown raw)') for b in blockers)
        lines.append(f'Data verification: execution unverified — {readable}.')
    else:
        lines.append('Data verification: no execution-grade verification is recorded for this event; treat execution as unverified.')
    lines.append(f'Data source: provider {event.get("provider") or NA}; delay {event.get("delay") or NA}; '
                 f'price basis {event.get("price_basis") or NA}; freshness {event.get("data_freshness") or NA}.')
    if signal is not None and tf_minutes is not None:
        expiry = signal + timedelta(minutes=tf_minutes + DELIVERY_GRACE_MIN)
        lines.append(f'Notification expiry: {_stamp(expiry)} (candle close + timeframe + {DELIVERY_GRACE_MIN} min; '
                     f'new events are only queued within timeframe + {ENQUEUE_GRACE_MIN} min). '
                     'This is a delivery limit, not how long the setup stays tradable.')
    else:
        lines.append(f'Notification expiry: {NA}.')
    lines.append('This email is a snapshot and cannot update itself; a later rebuild, invalidation or price move is not reflected here.')
    lines.append('Follow-up emails are not guaranteed: an invalidation, expiry or exit of this setup may never be emailed.')
    lines.append('Research only. No broker order is placed, and this alert does not manage your actual holdings. '
                 'Do not use email as your stop-loss mechanism.')
    return lines


def _config_for(event, config):
    if config is None:
        return None
    try:
        return config if config.hash == event.get('config_hash') else None
    except AttributeError:
        return None


def _rule_line(rule, event, cfg):
    bear = _bearish(event)
    close, line, atr, rr = (event.get(k) for k in ('planned_entry', 'trendline_value', 'atr', 'reward_risk'))
    if rule == 'confirmed_structure':
        anchors = event.get('anchors') or []
        prices = ', '.join(_num(a.get('price')) for a in anchors if isinstance(a, dict)) or NA
        return f'Confirmed A-B-C pivot structure ({"short" if bear else "long"}); anchor prices {prices}.'
    if rule == 'line_touch':
        tol = (f'±{_num(cfg.touch_atr * atr)} (touch {cfg.touch_atr:g} × ATR {_num(atr)})'
               if cfg is not None and isinstance(atr, (int, float)) else f'{NA} (ATR {_num(atr)})')
        return f"Trigger candle's range touched the A-C trendline at {_num(line)}; tolerance {tol}."
    if rule == 'directional_close':
        side, body = ('below', 'below its open') if bear else ('above', 'above its open')
        return (f'Trigger candle closed at {_num(close)}, {side} the trendline ({_num(line)}); the directional-close '
                f'rule ({body}) passed, but the candle\'s open is not recorded in this event.')
    if rule == 'previous_bar_break':
        level = 'low' if bear else 'high'
        return (f"Trigger close {_num(close)}: the prior-candle-break rule (beyond the prior candle's {level}) passed, "
                f"but that prior-candle {level} is not recorded in this event.")
    if rule == 'planned_reward_risk':
        minimum = f'{cfg.min_rr:g}:1' if cfg is not None else NA
        shown = f'{rr:.2f}:1' if isinstance(rr, (int, float)) else NA
        return f'Planned reward:risk {shown} before costs, at or above the configured minimum ({minimum}).'
    return f'{rule} (unrecognised rule identifier, shown raw).'


def _reasons(event, cfg):
    kind = event['event_type']
    if kind == 'TRIGGERED':
        rules = event.get('passed_rules') or []
        return [_rule_line(r, event, cfg) for r in rules] or ['No passed rules are recorded in this event.']
    if kind in WATCH_TYPES:
        reason = event.get('reason')
        lines = [WATCH_REASONS.get(reason, f'{reason} (unrecognised reason identifier, shown raw)') if reason else f'Measured reason: {NA}.']
        if kind == 'REVERSAL_WATCH':
            lines.append(f'Watched level: Bollinger middle band {_num(event.get("middle_band"))} on this candle; '
                         f'possible new direction: {event.get("direction") or NA}.')
            minimum = f'{cfg.min_rr:g}:1' if cfg is not None else 'the configured minimum'
            lines.append('Still missing: a new confirmed RZY structure, a trendline touch with a directional close beyond the prior candle, '
                         f'and planned reward:risk of at least {minimum}. No entry exists until a trigger email/event.')
        else:
            band = 'lower' if _bearish(event) else 'upper'
            lines.append(f'Watched level: {band} Bollinger band {_num(event.get(f"{band}_band"))}; '
                         f'measured move D {_num(event.get("D"))}; sequence {event.get("sequence_number") if event.get("sequence_number") is not None else NA}.')
            lines.append('Still missing: a confirmed reversal (a later close across the middle band, which is itself only a watch). '
                         'Exhaustion is a research heuristic, not an exit instruction.')
        return lines
    if kind == 'ENTRY_CANCELLED':
        failed = event.get('failed_rules') or []
        return [CANCEL_REASONS.get(r, f'{r} (unrecognised identifier, shown raw)') for r in failed] or [
            'Reason: ' + str(event.get('rejection_reason') or NA)]
    if kind in EXIT_TYPES:
        reason = event.get('reason')
        if kind == 'EXIT_PENDING':
            return ['A candle closed back through the structure trendline; the paper strategy exits at the next open. '
                    f'Paper state: {event.get("paper_state") or NA}.']
        return [f'Exit reason: ' + (EXIT_REASONS.get(reason, f'{reason} (unrecognised identifier, shown raw)') if reason else NA) + '.']
    return [f'Measured reason: {event.get("reason") or NA}.']


def _levels(event):
    kind = event['event_type']
    currency = event.get('currency') or 'currency not recorded'
    entry, stop, target = (event.get(k) for k in ('planned_entry', 'stop', 'primary_target'))
    lines = [f'Quote currency: {currency}.',
             f'Entry reference (trigger close, not a fill): {_num(entry)}',
             f'Structural stop: {_num(stop)}',
             f'Primary target: {_num(target)}']
    if event.get('runner_target') is not None:
        lines.append(f'Runner target: {_num(event.get("runner_target"))}')
    nums = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (entry, stop, target))
    if nums and entry:
        risk = abs(stop - entry)
        lines.append(f'Risk distance: {risk:.4f} per share ({risk / entry * 100:.2f}% of entry); '
                     f'target reward distance: {abs(target - entry):.4f} per share.')
    else:
        lines.append(f'Risk distance: {NA}.')
    rr = event.get('reward_risk')
    lines.append('Planned reward : risk: ' + (f'{rr:.2f}:1, gross before costs' if isinstance(rr, (int, float)) else NA))
    lines.append('Reward : risk compares the distance to the target with the distance to the stop. It is not a win probability; '
                 'a high reward:risk is not a high probability. Costs and actual fills change it.')
    lines.append('Nominal stop risk does not cap losses: a gap through the stop can fill well beyond it.')
    if kind != 'TRIGGERED':
        lines.insert(0, 'Original setup reference levels — not a new entry or current trailing stop.')
    return lines


def _exit_lines(event):
    lines = []
    if event['event_type'] == 'PAPER_EXIT':
        qty = event.get('exit_quantity')
        lines += [f'Paper exit price: {_num(event.get("exit_price"))}. Reason: {event.get("reason") or NA}.',
                  f'Exit quantity (fraction of position): {_num(qty)}; paper state after exit: {event.get("paper_state") or NA}.',
                  f'Paper entry fill: {_num(event.get("actual_paper_fill"))}; net P&L per unit so far: {_num(event.get("net_pnl_per_unit"))} (paper, after modelled costs).']
    else:
        lines += [f'Planned exit: next bar open (price not yet known). Action code: {event.get("position_action") or NA}.',
                  f'Paper entry fill: {_num(event.get("actual_paper_fill"))}.']
    return lines


def chart_url(event, base_url=CHART_BASE_URL):
    params = {'tf': event.get('timeframe') or ''}
    if event.get('occurrence_id'):
        params['rzy_event'] = event['occurrence_id']
    return f"{base_url.rstrip('/')}/stock/{quote(str(event['symbol']), safe='')}?{urlencode(params)}"


def sections(event, paper_blockers=(), *, evaluated_at=None, rendered_at=None, config=None, base_url=CHART_BASE_URL):
    kind = event['event_type']
    blockers = tuple(paper_blockers or ())
    rendered_at = _parse(rendered_at) if rendered_at is not None else datetime.now(timezone.utc)
    evaluated_at = _parse(evaluated_at) if evaluated_at is not None else None
    cfg = _config_for(event, config)
    out = [('Status & freshness', _status(event, blockers, evaluated_at, rendered_at))]
    if kind in EXIT_TYPES:
        out.append(('Exit', _exit_lines(event) + _reasons(event, cfg)))
        out.append(('Original setup levels', _levels(event)))
    elif kind in WATCH_TYPES:
        out.append(('What was measured & what is still missing', _reasons(event, cfg)))
    elif kind == 'ENTRY_CANCELLED':
        out.append(('Why the paper entry was cancelled', _reasons(event, cfg)))
        out.append(('Original setup levels', _levels(event)))
    else:
        out.append(('Why this triggered', _reasons(event, cfg)))
        out.append(('Setup levels', _levels(event)))
    out.append(('How to interpret this', [
        'A bearish setup looks for a falling price; a bullish setup looks for a rising price. These are conditional pattern signals, not instructions to trade.',
        'RZY is an independent rule-based research signal, not the AI signal. Historical evaluation has not demonstrated a trading edge.']))
    out.append(('Chart', [('link', 'Open the stock chart', chart_url(event, base_url)),
                          'The chart shows the CURRENT state, which may differ from this archived email. '
                          'Select the timeframe on the chart page; it opens on its default view.']))
    seq = str(event.get('sequence_number')) if event.get('sequence_confidence') == 'known' else 'unknown (history begins mid-trend)'
    out.append(('Technical details', [
        f'Event: {kind}; action code: {event.get("position_action", "WATCH_ONLY")}; sequence: {seq}.',
        f'Occurrence: {event.get("occurrence_id") or NA}; event ID: {event.get("event_id") or NA}; generation: {event.get("generation", NA)}.',
        f'Config hash: {event.get("config_hash") or NA}; strategy version: {event.get("strategy_version") or NA}; structure: {event.get("structure_id") or NA}.',
        'Paper blockers: ' + (', '.join(blockers) or 'none reported; fill checks still apply'),
        'Passed: ' + (', '.join(event.get('passed_rules') or []) or 'none recorded'),
        'Failed: ' + (', '.join(event.get('failed_rules') or []) or 'none recorded')]))
    out.append(('Preferences', ['Manage these independent email preferences in Settings → Short-term trading & Little RZY.']))
    return out


def _text_line(line):
    return f'{line[1]}: {line[2]}' if isinstance(line, tuple) else line


def render_event(event, paper_blockers=(), **kw):
    body = '\n\n'.join(title + '\n' + '\n'.join(_text_line(l) for l in lines) for title, lines in sections(event, paper_blockers, **kw))
    return event_subject(event) + '\n' + event_label(event) + '\n\n' + body + '\n'


def _html_line(line):
    if isinstance(line, tuple):
        return f'<p style="margin:8px 0;overflow-wrap:anywhere"><a href="{escape(line[2])}">{escape(line[1])}</a> ({escape(line[2])})</p>'
    return f'<p style="margin:8px 0;overflow-wrap:anywhere">{escape(line)}</p>'


def render_event_html(event, paper_blockers=(), **kw):
    cards = []
    for n, (title, lines) in enumerate(sections(event, paper_blockers, **kw)):
        small = title == 'Technical details'
        style = ('padding:16px 20px;border-top:1px solid #e2e8f0' + (';background:#fff7ed' if n == 0 else '')
                 + (';font-size:12px;color:#475569' if small else ''))
        heading = f'<h2 style="font-size:{"13" if small else "16"}px;margin:0 0 10px">{escape(title)}</h2>'
        if n == 0:   # The status heading leads the panel in bold.
            body = f'<p style="margin:0 0 8px;font-weight:bold">{escape(lines[0])}</p>' + ''.join(_html_line(l) for l in lines[1:])
        else:
            body = ''.join(_html_line(l) for l in lines)
        cards.append(f'<div style="{style}">{heading}{body}</div>')
    return ('<div style="max-width:640px;margin:auto;font:15px/1.5 Arial,sans-serif;color:#172033;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px">'
            '<div style="padding:20px;background:#eff6ff"><p style="margin:0;font-size:12px">LITTLE RZY · RESEARCH ALERT</p>'
            f'<h1 style="font-size:22px;margin:8px 0">{escape(str(event["symbol"]))} · {escape(str(event["timeframe"]))}</h1>'
            f'<p style="margin:0">{escape(event_label(event))}</p></div>' + ''.join(cards) + '</div>')
