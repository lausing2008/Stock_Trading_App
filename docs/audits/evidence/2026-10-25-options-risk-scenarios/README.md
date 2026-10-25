# Option risk-basis and scenario evidence

[Report](../../2026-10-25-options-risk-scenarios.md).

Offline native Chrome uses actual strategy cards, risk component, chart helper and global CSS with labeled historical discussion examples. It makes no market requests and does not authenticate to production. DOM tests separately cover interactive custom values and preserved contract/assignment state.

`layout.json` records 1449/1024/390 px document containment, both open tables, all four charts and successful keyboard scrolling. Images reset horizontal scrolling to the initial columns after verification so stock price / total P&L remain visible. `mobile-detail.png` is the initial covered-call section. `final-numeric-nowrap/` preserves the final numeric-cell refinement separately: signs and values stay together, with updated screenshots/metrics. Original images are retained.

Local validation: 980 frontend tests / 82 files, full typecheck and whitespace clean. Backend unchanged; preceding 5,912 passed / 26 skipped is not a fresh run. `production.json`, `backend-source.json` and `served.json` record final release `bbb7ce1b` / build `-ebXhMCK6FJl0xd5Sk13W`: 15 healthy, 12 source/shared digest matches, all 14 non-frontend start times unchanged across the initial and follow-up frontend rollouts, new served risk UI/tracker/tutorial and retained rollback. `final-cents/` preserves the exact-zero/extreme-input boundary witness and its separate deployment script. No backend restart, new provider capture or authenticated browser acceptance is claimed.

Reproduce with frontend dependencies, Python Playwright and `/usr/bin/google-chrome` installed:

```bash
cd frontend
node ../docs/audits/evidence/2026-10-25-options-risk-scenarios/render_fixture.cjs
python3 ../docs/audits/evidence/2026-10-25-options-risk-scenarios/check_layout.py
```

Outputs use `/tmp/options-risk-*` without overwriting saved evidence. Chrome launch may require permission in a sandbox.
