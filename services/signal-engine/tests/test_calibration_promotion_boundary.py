"""Registered legacy writers are research-only, independently of old core-function tests."""
import ast
from pathlib import Path
from datetime import date
import pytest
from src.api.calibration_safety import evidence_eligible, research_only

WRITERS = ['calibrate_ml_weight','calibrate_ta_weights','calibrate_conviction_weights',
           'outcomes_calibrate_apply','tune_sell_pillars','tune_style_profiles','tune_strategy','signal_watchdog']


@pytest.mark.parametrize('name',WRITERS)
def test_real_writer_has_guard_and_registered_callable_cannot_mutate(name):
    source = Path(__file__).resolve().parents[1]/'src/api/calibration.py'
    node = next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name==name)
    assert any(isinstance(d,ast.Name) and d.id=='research_only' for d in node.decorator_list)
    # Retain the real function body and safe decorator, replace arguments/other decorators only.
    node.decorator_list=[ast.Name(id='research_only',ctx=ast.Load())]
    node.args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[])
    ns={'research_only':research_only}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(source),'exec'),ns)
    result=ns[name]()  # legacy body has no dependencies supplied; any execution would fail
    assert result['status']=='research_only' and not result['applied']
    assert result['existing_settings_changed'] is False


@pytest.mark.parametrize('status',['unverified','provisional',None])
def test_unverified_records_cannot_be_promotion_evidence(status):
    row={'signal_date':date(2026,1,1),'entry_date':date(2026,1,2),'exit_date':date(2026,1,9),'is_correct':True,'evidence_status':status}
    assert not evidence_eligible(row)
    row['evidence_status']='verified'
    assert evidence_eligible(row)
    row['entry_date']=row['signal_date']
    assert not evidence_eligible(row)


def test_original_negative_first_tune_case_cannot_write():
    from test_calibrate_ml_weight_ratchet import _build_fixture, _run_core
    rows, prices = _build_fixture([(.8,.8,100,90)]*100,[(.8,.8,100,90)]*40)
    result, history, writes = _run_core(rows, prices, None)
    assert not result['applied'] and not history and not writes


def test_one_signal_date_cannot_be_both_training_and_validation():
    from test_calibrate_ml_weight_ratchet import _make_row, _calibration_helpers
    rows,prices=[],[]
    for sid in range(100):
        row, bars = _make_row(sid,date(2026,1,2),.8,.8,100,110)
        rows.append(row)
        prices.extend(bars)
    result=_calibration_helpers()['_ml_weight_sweep'](rows,prices,{}, {},None)
    assert result['split']['train_n']==0
    assert not result['decision']['would_apply']
