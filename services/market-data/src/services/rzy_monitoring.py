"""Read-only descriptive operational measures; no trading readiness promotion."""
from collections import Counter
from math import ceil
from statistics import median
from .rzy_data import utc, MINUTES


def distribution(values):
    values=sorted(values)
    return dict(samples=len(values),median=median(values) if values else None,
        p95=values[ceil(len(values)*.95)-1] if values else None,
        max=values[-1] if values else None)


def monitoring_report(occurrences, mail, snapshots, now, current_since):
    """Incremental trigger population, including unmailed signals. Never infer delivery
    from a queue row or pool replay/bootstrap latency with newly observed triggers.
    The caller supplies a single explicit observation window for occurrences and mail.
    """
    from .rzy_workflow import snapshot_is_current
    mail_by_event={r.event_id:r for r in mail if r.alert_type=='little_rzy'}
    by_tf={}
    for tf in sorted({r.timeframe for r in occurrences}):
        signals=[r for r in occurrences if r.timeframe==tf and r.event_type=='TRIGGERED' and r.observed_in=='incremental']
        stages={'detection':[],'queue':[],'provider_acceptance':[]}; outcomes=Counter(); malformed=0
        for r in signals:
            if r.signal_time is None: malformed+=1; continue
            detected=(utc(r.first_seen_at)-utc(r.signal_time)).total_seconds()
            if detected<0: malformed+=1; continue
            stages['detection'].append(detected)
            if r.queued_at:
                delta=(utc(r.queued_at)-utc(r.signal_time)).total_seconds()
                if delta>=0: stages['queue'].append(delta)
            # One signal may have multiple recipients; acceptance samples are per receipt,
            # detection/queue samples per occurrence. Label the differing denominator.
            matching=[m for m in mail_by_event.values() if m.event_id.endswith(':'+r.occurrence_id)]
            if not matching: outcomes['not_queued' if r.queued_at is None else 'receipt_outside_window']+=1
            for m in matching:
                outcomes[m.state]+=1
                if m.accepted_at:
                    delta=(utc(m.accepted_at)-utc(r.signal_time)).total_seconds()
                    if delta>=0: stages['provider_acceptance'].append(delta)
        by_tf[tf]=dict(trigger_occurrences=len(signals),invalid_timestamps=malformed,
            too_late_to_queue=sum(v>(MINUTES[tf]+3)*60 for v in stages['detection']),
            seconds_from_close={k:distribution(v) for k,v in stages.items()},receipt_outcomes=dict(outcomes))
    streams=[]
    for row in snapshots:
        health=(row.summary or {}).get('health') or {}
        current=snapshot_is_current(row,current_since)
        streams.append(dict(symbol=row.symbol,timeframe=row.timeframe,status=row.status if current else 'snapshot_expired',
            checked_at=row.checked_at.isoformat(),current=current,**health))
    return dict(by_timeframe=by_tf,streams=streams,
        readiness='research_only',execution_ready=False,
        blockers=['Execution-grade provider timing and price basis are unverified.',
                  'Short availability and borrow evidence are unavailable.',
                  'Full-session latency/completeness acceptance and forward after-cost edge are not established.'],
        note='Last 20 hours. Detection and queue samples are per incremental trigger occurrence; acceptance samples and receipt states count recipient receipts. Not_queued and receipt_outside_window count occurrences, not recipients; neither identifies a cause. Provider acceptance is not inbox delivery. Exact completed-bar ingestion time is not stored; first write can be a forming candle. No signals is not proof of complete detection.')
