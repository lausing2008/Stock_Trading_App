import json
import re
import subprocess
import urllib.request

expected = subprocess.check_output(
    ['docker', 'exec', 'stockai-frontend-1', 'cat', '/app/.next/BUILD_ID'], text=True,
).strip()
pages = []
for path, markers in (
    ('/stock/MU', ('Options decision desk', 'Contract research leads', 'Leading screen fit', 'Compare this contract')),
    ('/improvements', ('OPTIONS-RESEARCH-LEADS-DESK',)),
    ('/options-dark-pool-tutorial', ('Contract research leads', 'This ranks quote quality')),
):
    html = urllib.request.urlopen('https://lausing.com' + path, timeout=30).read().decode()
    data = json.loads(re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html).group(1))
    assert data['buildId'] == expected
    script_paths = re.findall(r'<script[^>]+src="([^"]+)"', html)
    scripts = '\n'.join(
        urllib.request.urlopen('https://lausing.com' + source, timeout=30).read().decode()
        for source in script_paths if source.startswith('/_next/')
    )
    assert all(marker in scripts for marker in markers), (path, [marker for marker in markers if marker not in scripts])
    pages.append(path)
css_paths = re.findall(r'<link[^>]+href="([^"]+\.css)"', html)
styles = '\n'.join(
    urllib.request.urlopen('https://lausing.com' + source, timeout=30).read().decode()
    for source in css_paths if source.startswith('/_next/')
)
assert '.option-research-leads-grid' in styles
assert '.option-contract-selection select' in styles
print('SERVED_BUILD=' + json.dumps({'build_id': expected, 'pages': pages, 'lead_styles_verified': True}))
