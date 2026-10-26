"""Real storage/ingest/report gates, with synthetic external provider data."""
import json,os,pathlib,subprocess,sys
import pytest
PROBE=pathlib.Path(__file__).with_name('_bar_ingest_observations_probe.py')
CHECKS=['conserved_session_counts','first_observed_post_close','rollback_atomic','venue_dates','latency_anchors','calendar_boundaries']
if os.environ.get('RZY_PG_REQUIRED')=='1' and not os.environ.get('RZY_PG_URL'):
    raise RuntimeError('RZY_PG_REQUIRED needs a disposable local RZY_PG_URL')
@pytest.fixture(scope='module')
def evidence():
    env={k:v for k,v in os.environ.items() if k!='RZY_PG_URL'}
    r=subprocess.run([sys.executable,str(PROBE)],capture_output=True,text=True,env=env,timeout=90)
    assert r.returncode==0,r.stdout[-1500:]+r.stderr[-3500:]
    return json.loads(r.stdout.strip().splitlines()[-1])
@pytest.mark.parametrize('check',CHECKS)
def test_real_observations(evidence,check):assert evidence[check]
@pytest.mark.skipif(not os.environ.get('RZY_PG_URL'),reason='Disposable PostgreSQL URL absent; required in integration target')
def test_real_ingest_and_report_on_postgresql():
    r=subprocess.run([sys.executable,str(PROBE)],capture_output=True,text=True,timeout=150)
    assert r.returncode==0,r.stdout[-1500:]+r.stderr[-3500:]
    result=json.loads(r.stdout.strip().splitlines()[-1])
    assert all(result.get(k) for k in CHECKS+['real_ingest_hook','telemetry_failure_preserves_prices','read_only_report_and_dst','inactive_retention'])

def test_capture_refuses_existing_evidence_before_any_docker_command(tmp_path):
    dest=tmp_path/'evidence';dest.mkdir();(dest/'manifest.json').write_text('preserved')
    fake=tmp_path/'bin';fake.mkdir();marker=tmp_path/'docker-called'
    docker=fake/'docker';docker.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 77\n');docker.chmod(0o700)
    env=dict(os.environ,PATH=str(fake)+os.pathsep+os.environ['PATH'])
    script=pathlib.Path(__file__).resolve().parents[3]/'scripts/capture_rzy_session_evidence.sh'
    r=subprocess.run(['/bin/bash',str(script),'2026-10-23',str(dest)],env=env,capture_output=True,text=True,timeout=10)
    assert r.returncode==1 and 'Evidence destination already exists' in r.stderr
    assert not marker.exists() and (dest/'manifest.json').read_text()=='preserved'

@pytest.mark.parametrize('failure',['none','copy','image'])
def test_capture_verifies_artifacts_and_container_identity(tmp_path,failure):
    import hashlib
    root=pathlib.Path(__file__).resolve().parents[3]
    report=tmp_path/'fake-report';report.mkdir()
    payload=b'{"fixture":true}\n';(report/'report.json').write_bytes(payload)
    (report/'manifest.json').write_text(json.dumps({'files':{'report.json':hashlib.sha256(payload).hexdigest()}}))
    fake=tmp_path/'bin';fake.mkdir()
    docker=fake/'docker'
    docker.write_text('''#!/usr/bin/env python3
import sys,os,pathlib,shutil
args=sys.argv[1:]; text=' '.join(args);base=pathlib.Path(os.environ['FAKE_ROOT'])
if args[0]=='inspect':
 if '.Image' in text:
  f=base/'image-count';n=int(f.read_text())+1 if f.exists() else 1;f.write_text(str(n))
  print('sha256:changed' if n>1 and os.environ['FAKE_FAILURE']=='image' else 'sha256:fixture')
 elif 'StartedAt' in text:print('2026-10-23T12:00:00Z')
 else:print('fixture-release')
elif args[0]=='cp' and args[1].startswith('stockai-'):
 if os.environ['FAKE_FAILURE']=='copy':sys.exit(1)
 shutil.copytree(os.environ['FAKE_REPORT'],args[2],dirs_exist_ok=True)
elif args[0]=='exec':
 if '\\\\copy' in text:print('symbol\\nOBS')
 elif 'daily_ingest_records' in text:print('0 1')
''');docker.chmod(0o700)
    clock=fake/'date';clock.write_text('#!/bin/sh\nif [ "$#" -eq 1 ] && [ "$1" = "+%s" ]; then echo 9999999999; else exec /bin/date "$@"; fi\n');clock.chmod(0o700)
    env=dict(os.environ,PATH=str(fake)+os.pathsep+os.environ['PATH'],FAKE_ROOT=str(tmp_path),FAKE_REPORT=str(report),FAKE_FAILURE=failure,
        STOCKAI_REPO=str(root),DEADLINE_HOURS='0',DEPLOY_LOCK_FILE=str(tmp_path/'lock'),DEPLOY_STATUS_FILE=str(tmp_path/'status'))
    env.pop('DEPLOY_LOCK_TOKEN',None)
    dest=tmp_path/'captured'
    r=subprocess.run(['/bin/bash',str(root/'scripts/capture_rzy_session_evidence.sh'),'2026-10-23',str(dest)],env=env,capture_output=True,text=True,timeout=20)
    assert r.returncode==(0 if failure=='none' else 1),r.stdout+r.stderr
    captured=json.loads((dest/'capture.json').read_text())
    assert captured['capture_complete']==(failure=='none')
    assert not captured['post_close_confirmed']
    assert captured['report_manifest_verified']==(failure!='copy')
