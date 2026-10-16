from datetime import datetime,timedelta
from src.calibration_research import Observation,compare


def rows(status='verified'):
    start=datetime(2026,1,1)
    return [Observation(i,f'S{i%5}','US','SWING','BUY',5,60,start+timedelta(days=i//3),
                        start+timedelta(days=i//3+6),bool(i%2),status) for i in range(90)]


def test_unverified_history_cannot_qualify_and_provisional_is_explicit():
    now=datetime(2026,5,1)
    assert compare(rows('unverified'),now=now)['eligible']==0
    assert compare(rows('provisional'),now=now)['eligible']==0
    r=compare(rows('provisional'),now=now,include_provisional=True)
    assert r['mode']=='provisional_research' and not r['production_changed']
    assert r['candidates'] and all(x['paired_count']>0 for x in r['candidates'])


def test_future_label_availability_cannot_enter_any_training_fold():
    now=datetime(2026,5,1)
    unavailable=[Observation(r.signal_id,r.symbol,r.market,r.horizon,r.direction,r.window,
        r.score,r.issued_at,now-timedelta(days=1),r.correct,r.evidence_status) for r in rows()]
    r=compare(unavailable,now=now,min_train=5)
    assert r['status']=='insufficient_chronological_training'
    assert all(x['forecast_count']==0 and x['paired_brier'] is None for x in r['candidates'])


def test_comparisons_share_the_same_heldout_rows_and_keep_windows_separate():
    r=compare(rows(),now=datetime(2026,5,1),min_train=10)
    assert r['status']=='research_only'
    assert len({x['paired_count'] for x in r['candidates']})==1
    assert all(0 <= x['paired_brier'] <= 1 for x in r['candidates'])
    sparse=[Observation(x.signal_id,x.symbol,x.market,x.horizon,x.direction,x.signal_id+1,
            x.score,x.issued_at,x.resolved_at,x.correct,x.evidence_status) for x in rows()]
    assert all(x['forecast_count']==0 for x in compare(sparse,now=datetime(2026,5,1),min_train=2)['candidates'])


def test_duplicate_labels_and_future_resolutions_are_excluded():
    data=rows(); extra=data[:3]+[Observation(1000,'F','US','SWING','BUY',5,60,datetime(2026,3,1),datetime(2027,1,1),True,'verified')]
    r=compare(data+extra,now=datetime(2026,5,1))
    assert r['excluded']['duplicate_signal_window']==3
    assert r['excluded']['label_clock_unavailable_or_invalid']==1


def test_missing_prediction_snapshot_is_not_repaired_from_a_current_score():
    r=Observation(1,'A','US','SWING','BUY',5,99,None,datetime(2026,3,1),True,'verified')
    result=compare([r],now=datetime(2026,5,1))
    assert result['eligible']==0 and result['excluded']['missing_or_mismatched_prediction_snapshot']==1


def test_database_adapter_uses_frozen_scores_and_clocks_not_current_signal_state():
    import subprocess,sys
    from pathlib import Path
    p=subprocess.run([sys.executable,str(Path(__file__).with_name('_calibration_store_probe.py'))],capture_output=True,text=True,timeout=60)
    assert p.returncode==0,p.stdout+p.stderr
