"""Offline acceptance checks for EC-01/EC-02; no network or production changes."""
import ast
import copy
import importlib.util
import json
import tempfile
from pathlib import Path
from sqlalchemy import create_engine, text

ROOT=Path(__file__).resolve().parents[3]

def load(name,tree,env):
    node=copy.deepcopy(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'<actual-migration-functions>','exec'),env)

def migration():
    tree=ast.parse((ROOT/'shared/db/session.py').read_text())
    watermark=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                   and any(isinstance(t,ast.Name) and t.id=='_PRICE_ALERT_DELIVERY_WATERMARK' for t in n.targets))
    with tempfile.TemporaryDirectory() as d:
        engine=create_engine('sqlite:///'+str(Path(d)/'verify.db'))
        env=dict(engine=engine,text=text,_PRICE_ALERT_DELIVERY_WATERMARK=watermark,print=lambda *args:None)
        load('_apply_once',tree,env);load('_apply_one_shot_migrations',tree,env)
        with engine.begin() as c:
            c.execute(text('CREATE TABLE price_alerts (id INTEGER PRIMARY KEY, triggered BOOLEAN, triggered_at TEXT, last_sent_at TEXT)'))
            c.execute(text("INSERT INTO price_alerts VALUES (1,1,'2026-09-20 12:00:00',NULL),(2,1,'2026-09-29 12:00:00',NULL)"))
        run=env['_apply_one_shot_migrations'];run()
        with engine.begin() as c:
            c.execute(text("INSERT INTO price_alerts VALUES (3,1,'2026-09-21 12:00:00',NULL)"))
        for _ in range(3):run()
        with engine.begin() as c:
            rows=dict(c.execute(text('SELECT id,last_sent_at FROM price_alerts')).all())
            assert rows=={1:'2026-09-20 12:00:00',2:None,3:None},rows
            c.execute(text('DROP TABLE applied_migrations'))
        run()
        with engine.begin() as c:
            assert c.execute(text('SELECT last_sent_at FROM price_alerts WHERE id=2')).scalar() is None
        env['_apply_once']('failure-probe','UPDATE table_that_does_not_exist SET x=1')
        with engine.begin() as c:
            assert c.execute(text("SELECT count(*) FROM applied_migrations WHERE name='failure-probe'")).scalar()==0
        env['_apply_once']('failure-probe','UPDATE price_alerts SET triggered=0 WHERE id=2')
        with engine.begin() as c:
            assert c.execute(text('SELECT triggered FROM price_alerts WHERE id=2')).scalar()==0
            assert c.execute(text("SELECT count(*) FROM applied_migrations WHERE name='failure-probe'")).scalar()==1
        engine.dispose()
    return dict(watermark=watermark,legacy_closed=True,pending_survives_restarts=True,
                late_legacy_proves_ledger=True,pending_survives_ledger_loss=True,
                failed_statement_rolls_back_claim=True,retry_after_failure_applies=True)

def render():
    p=Path(__file__).with_name('2026-09-28-email-fix-closure-checks.py')
    spec=importlib.util.spec_from_file_location('prior_closure',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    r=m.run_fixture('retry_price',80.0)
    sent=[]
    fn=m.old.a.load(m.old.a.EMAIL,'send_price_alert_email',{'send_email':lambda *args:sent.append(args) or True})
    fn(**r['sent'][0])
    subject,html,body=sent[0][1:]
    assert subject.startswith('Price Alert (delayed):') and 'had risen above' in subject
    assert 'is now 80.0000 (risen above' not in body
    assert 'Current price is 80.0000' in body and 'back below the threshold' in body
    assert r['sent'][0]['event_at'] in body
    return dict(subject=subject,body=body,event_at_wired=True,false_current_sentence_absent=True)

if __name__=='__main__':
    print(json.dumps(dict(migration=migration(),render=render()),indent=2))
