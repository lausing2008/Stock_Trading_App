# Option refresh evidence

[Report](../../2026-10-25-options-snapshot-refresh.md).

- `current-endpoint-capability.json`: two bounded requests verified actual current-contract quote/Greek fields and stock tape time. No credentials or user positions.
- `full-capture-capability.json`: complete real MU capture into ephemeral memory; 11,722 contracts / 24 pages / 807,506 compressed bytes. Its ID is historical and cannot be used as a live app snapshot. The first oversized-format failure was not published.
- Native browser fixtures render the actual control and stylesheet, using the saved metadata and a frozen clock. They do not authenticate to production or request quotes. Interaction and data-routing checks are separate DOM/backend tests.
- Final test, native layout and production verification records are added below on completion. The earlier MU/archive/Cboe evidence is unchanged.


Reproduce the offline layout from repository root (frontend dependencies, Python Playwright and Chrome required):

```bash
cd frontend
node ../docs/audits/evidence/2026-10-25-options-refresh/render_fixture.cjs
python3 ../docs/audits/evidence/2026-10-25-options-refresh/check_layout.py
```

Outputs use `/tmp/options-refresh-*` and do not overwrite saved evidence. Chrome launch may need permission in a sandbox. All recorded times and the fixture clock are explicit; recorded snapshots are not active application cache IDs.
