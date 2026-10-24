from pathlib import Path
import subprocess,sys,json
source=Path('src/services/options_strategies.py').read_text()
variants={
 'wide_spread_screen': source.replace('if spread > 20:', 'if False:'),
 'same_day_expiry_screen': source.replace('or expiry <= today.isoformat()', 'or False'),
}
results={}
for name,variant in variants.items():
 assert variant != source
 code="import sys,types,pytest\nm=types.ModuleType('src.services.options_strategies')\nexec("+repr(variant)+",m.__dict__)\nsys.modules[m.__name__]=m\nraise SystemExit(pytest.main(['-q','tests/test_covered_call_candidates.py']))\n"
 p=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
 Path('/tmp/candidate-guard-'+name+'.log').write_text(p.stdout+p.stderr)
 assert p.returncode==1,(name,p.returncode,(p.stdout+p.stderr)[-1000:])
 results[name]={'exit_code':p.returncode,'detected':True,'summary':p.stdout.strip().splitlines()[-1]}
Path('/tmp/candidate-guard-results.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results))
