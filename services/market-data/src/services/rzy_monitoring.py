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


def monitoring_report(occurrences, mail, snapshots, now, current_since,post_close_writes=None):
    """Incremental trigger population, including unmailed signals. Never infer delivery
    from a queue row or pool replay/bootstrap latency with newly observed triggers.
    The caller supplies a single explicit observation window for occurrences and mail.
    """
    from .rzy_workflow import snapshot_is_current
    mail_by_event={r.event_id:r for r in mail if r.alert_type=='little_rzy'}
    by_tf={}
    for tf in sorted({r.timeframe for r in occurrences}):
        signals=[r for r in occurrences if r.timeframe==tf and r.event_type=='TRIGGERED' and r.observed_in=='incremental']
        stages={'detection':[],'queue':[],'provider_acceptance':[],'observed_post_close_write':[]}; outcomes=Counter(); malformed=0
        write_to_detection=[]
        missing_write=write_after_detection=0
        for r in signals:
            if r.signal_time is None: malformed+=1; continue
            detected=(utc(r.first_seen_at)-utc(r.signal_time)).total_seconds()
            if detected<0: malformed+=1; continue
            stages['detection'].append(detected)
            if tf=='5m':
                stamp=utc(r.signal_time).replace(tzinfo=None)
                observed=(post_close_writes or {}).get((getattr(r,'symbol',None),stamp))
                if observed is None:missing_write+=1
                elif utc(observed)>utc(r.first_seen_at) or utc(observed)<utc(r.signal_time):write_after_detection+=1
                else:
                    stages['observed_post_close_write'].append((utc(observed)-utc(r.signal_time)).total_seconds())
                    write_to_detection.append((utc(r.first_seen_at)-utc(observed)).total_seconds())
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
            post_close_write_missing=missing_write,post_close_write_timestamp_excluded=write_after_detection,
            segments_seconds={'observed_write_to_detection':distribution(write_to_detection)},
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
        note='Last 20 hours. Detection/queue samples count incremental trigger occurrences; acceptance samples and receipt states count recipient receipts. Not_queued and receipt_outside_window count occurrences, not recipients, without asserting a cause. Observed post-close write timing is available only for native 5m bars since instrumentation began, not first-ever ingestion, provider finality or transaction commit time. Missing/late observation timestamps are excluded, never zero-filled. Provider acceptance is not inbox delivery. No signals is not proof of complete detection.')
