# Options Learning and table layout evidence

[Implementation and rollout report](../../2026-10-24-options-learning-and-market-pressure-layout.md).

Native Chrome witnesses use the actual component/chart code and styles with synthetic feeds, without credentials or production requests. Tutorial visual fixtures render the exported content; actual route guards are tested separately. Original user and earlier audit evidence are unchanged.

## Checks

- Full frontend: **945 passed across 77 files**, full typecheck clean. Focused tutorial/Market Pressure/navigation checks: 106 passed.
- `layout-before.json` and `layout-after.json`: 1449/1024/390 px geometry. The former 1024 px document overflow and 226 px desktop prints card are repaired. At 390 px only the named table region scrolls; ArrowRight moves it 320 px. `tableEscapesCard` measures the table's geometry, so it is intentionally true inside the mobile scroller; card and document widths remain contained.
- `tutorial-layout.json`: twelve anchors, practice expansion/three sample rows and no document overflow at all three widths. Sorting/selection and route access guards have separate DOM tests.
- Before/after PNGs are offline synthetic witnesses, not authenticated production screenshots. The lesson/practice images use explicitly fictional contracts.
- Backend source/settings/polling are unchanged. Production build and final verification are recorded in the report and added JSON files on rollout.

## Reproduce locally

With frontend dependencies, Python Playwright and `/usr/bin/google-chrome` installed, run from repository root:

```bash
python3 docs/audits/evidence/2026-10-24-options-learning-layout/prepare_baseline.py
cd frontend
node ../docs/audits/evidence/2026-10-24-options-learning-layout/render_fixture.cjs before
node ../docs/audits/evidence/2026-10-24-options-learning-layout/render_fixture.cjs after
python3 ../docs/audits/evidence/2026-10-24-options-learning-layout/check_layout.py before
python3 ../docs/audits/evidence/2026-10-24-options-learning-layout/check_layout.py after
python3 ../docs/audits/evidence/2026-10-24-options-learning-layout/check_tutorial.py
```

Outputs use `/tmp/options-layout-*` and `/tmp/options-tutorial-*`; original saved evidence is not overwritten. Chrome may require permission to launch in a restricted execution environment. The fixed baseline is `1a8d4c1e9ded790a8badd2d8225d3eef613474b7`.
