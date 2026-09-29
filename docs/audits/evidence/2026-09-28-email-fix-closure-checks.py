"""Offline closure checks at 174192f9; no application or production mutation.

Reuses controlled fixtures from the prior audit, replacing their defect assertions
with current acceptance checks. Application function bodies remain unchanged.
Migration probe executes the exact UPDATE on a minimal temporary SQLite schema.
"""
import ast
import copy
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
spec=importlib.util.spec_from_file_location('prior_followup',HERE/'2026-09-28-email-remediation-followup.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)

class AdaptFixture(ast.NodeTransformer):
    def visit_Assert(self,node):
        return ast.copy_location(ast.Pass(),node)
    def visit_Constant(self,node):
        if isinstance(node.value,float) and node.value==123.0:
            return ast.copy_location(ast.Name(id='probe_price',ctx=ast.Load()),node)
        return node
    def visit_Call(self,node):
        self.generic_visit(node)
        if isinstance(node.func,ast.Name) and node.func.id=='MagicMock':
            for kw in node.keywords:
                if (kw.arg=='side_effect' and isinstance(kw.value,ast.Call)
                    and isinstance(kw.value.func,ast.Name) and kw.value.func.id=='RuntimeError'
                    and kw.value.args[0].value=='controlled rendering failure'):
                    kw.value=ast.List(elts=[kw.value,ast.Constant(True)],ctx=ast.Load())
        return node

def run_fixture(name,price=123.0):
    tree=ast.parse(Path(old.__file__).read_text())
    fn=copy.deepcopy(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name))
    fn=AdaptFixture().visit(fn)
    fn.body[-1]=ast.Return(value=ast.Call(func=ast.Name(id='locals',ctx=ast.Load()),args=[],keywords=[]))
    env=dict(vars(old),probe_price=price)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'<adapted-audit-fixture>','exec'),env)
    return env[name]()

def checks():
    r=run_fixture('signal_batch')
    states=[x.last_signal for x in r['alerts']]
    assert r['send'].call_count==2 and not r['errors'] and states==['BUY','SELL'],states
    good=run_fixture('retry_price')
    assert good['requested']==['RETRY'] and good['sent'][0]['price']==123.0
    assert 'Delayed notification' in good['sent'][0]['note']
    missing=run_fixture('retry_price',None)
    assert missing['sent']==[]
    crossed_back=run_fixture('retry_price',80.0)
    rendered=[]
    render=old.a.load(old.a.EMAIL,'send_price_alert_email',{'send_email':lambda *args:rendered.append(args) or True})
    render(**crossed_back['sent'][0])
    assert 'risen above' in rendered[0][1] and '80.0000' in rendered[0][3]
    env,_=old.a.environment()
    classify=old.a.load(old.a.SCHED,'_classify_flow_side',env)
    assert classify(100,None)[0]=='unknown' and classify(100,0)[0]=='ask'
    p=ROOT/'services/market-data/tests/test_ea05_every_alert_type_is_enforced.py'
    spec=importlib.util.spec_from_file_location('new_ratchet',p)
    ratchet=importlib.util.module_from_spec(spec);spec.loader.exec_module(ratchet)
    ratchet._SCHED_CODE=ratchet._executable('# _may_send(session, user, "brand_new_type")')
    ratchet._PTE_CODE=''
    assert not ratchet._is_enforced('brand_new_type')
    return {'signal_mixed_batch':{'sender_calls':r['send'].call_count,'states':states,'outer_errors':r['errors']},
            'price_retry':{'requested_symbols':good['requested'],'observed_price':good['sent'][0]['price'],
                           'missing_quote_sends':len(missing['sent'])},
            'unknown_side_fixed':True,'comment_only_ratchet_rejected':True,
            'crossed_back_retry_semantic_residual':{'subject':rendered[0][1],'body':rendered[0][3]}}

def migration():
    tree=ast.parse((ROOT/'shared/db/session.py').read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_apply_isolated_ddl')
    assignment=next(n for n in fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='statements' for t in n.targets))
    sql=dict(ast.literal_eval(assignment.value))['legacy price-alert delivery closeout']
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE price_alerts (id INTEGER PRIMARY KEY, triggered BOOLEAN, triggered_at TEXT, last_sent_at TEXT)')
        db.execute("INSERT INTO price_alerts VALUES (1,1,'2026-09-20T12:00:00',NULL)")
        db.execute(sql)
        db.execute("INSERT INTO price_alerts VALUES (2,1,'2026-09-29T12:00:00',NULL)")
        before=db.execute('SELECT last_sent_at FROM price_alerts WHERE id=2').fetchone()[0]
        # A later init_db invokes the same unversioned UPDATE again.
        db.execute(sql)
        after=db.execute('SELECT last_sent_at FROM price_alerts WHERE id=2').fetchone()[0]
    assert before is None and after=='2026-09-29T12:00:00'
    return {'defect_reproduced':True,'sql':sql,'later_unsent_row_before_restart':before,
            'later_unsent_row_after_restart':after,'transport_calls':0,
            'scope':'exact UPDATE, minimal SQLite table; startup invocation verified in source'}

if __name__=='__main__':
    print(json.dumps({'closure_checks':checks(),'migration_restart':migration()},indent=2))
