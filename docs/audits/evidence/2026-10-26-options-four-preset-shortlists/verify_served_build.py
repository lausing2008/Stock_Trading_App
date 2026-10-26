import json
import re
import subprocess
import urllib.request

expected = subprocess.check_output(['docker', 'exec', 'stockai-frontend-1', 'cat', '/app/.next/BUILD_ID'], text=True).strip()
for path, markers in (
    ('/stock/MU', ('Preset nearest-strike reference', 'Showing', 'returned alternatives', 'not an automatic best pick')),
    ('/improvements', ('OPTIONS-FOUR-PRESET-SHORTLISTS',)),
    ('/options-dark-pool-tutorial', ('The first plan compares contracts for all four strategies',)),
):
    html = urllib.request.urlopen('https://lausing.com' + path, timeout=30).read().decode()
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
    assert match, path
    assert json.loads(match.group(1))['buildId'] == expected
    sources = re.findall(r'<script[^>]+src="([^"]+)"', html)
    scripts = '\n'.join(urllib.request.urlopen('https://lausing.com' + source, timeout=30).read().decode()
                        for source in sources if source.startswith('/_next/'))
    assert all(marker in scripts for marker in markers), (path, [marker for marker in markers if marker not in scripts])
print('SERVED_BUILD=' + json.dumps({'build_id': expected, 'pages_verified': 3}))
