"""Coverage required by the October 11–18 recommendations, without enabling mail."""
import subprocess
import sys
from pathlib import Path
import pytest
from src.services.company_impact_watch import category, statement_status


def test_real_benchmark_pipeline_and_owned_watchlist_audience():
    run=subprocess.run([sys.executable,str(Path(__file__).with_name('_alert_recommendations_completion_probe.py'))],capture_output=True,text=True,timeout=120)
    assert run.returncode==0,run.stdout+run.stderr


@pytest.mark.parametrize('title,kind,stage',[
    ('Microsoft may acquire a software company','acquisition','reported_speculation'),
    ('Microsoft denies merger talks','acquisition','reported_denial'),
    ('Microsoft completes acquisition of a software company','acquisition','reported_completed'),
    ('Microsoft signs a definitive merger agreement','acquisition','reported_signed'),
    ('Microsoft announces a public offering','financing','reported_announcement'),
    ('Regulator approves Microsoft merger','acquisition','reported_approval'),
    ('Microsoft signs a contract','contract','reported_signed'),
])
def test_company_stage_is_a_reported_claim_not_confirmed_effect(title,kind,stage):
    assert category(title)==kind
    assert statement_status(title)==stage


def test_routine_commentary_is_not_a_financing_or_acquisition_event():
    assert category('Microsoft shares trade higher in morning commentary') is None
