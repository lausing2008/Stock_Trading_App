# MU covered-call selection evidence

[Report](../../2026-10-24-mu-covered-call-selection.md).

- `archive_probe.py` / `archive-probe.json`: read-only MU November 20 sample, latest archive session October 22. Time limit 15 seconds; at most 25 selected public-market contract rows; no user positions or credentials.
- `cboe_probe.py` / `cboe-probe.json`: independent unauthenticated public delayed-chain presence check. Both near-price and distant exact identifiers exist at the recorded check. The timestamp is preserved as reported without inventing its timezone. No production feed integration or Fidelity account check.
- Regression tests use synthetic fixtures; no claim of best expected return. Final checks: backend 5,868 passed / 26 skipped, frontend 949 passed, full typecheck clean. `production.json`, `backend-source.json`, `served.json` and `deployed-replay.json` record the deployed `4c388f91` release: 15 healthy, 12 source/shared digests match, 13 unrelated start times unchanged, served new build, and real archived MU input selecting $880 with $875/$885/$870 alternatives. Rollback retained.

The public Cboe JSON endpoint is a one-off diagnostic source. Re-running it produces a new point-in-time result and must not overwrite this saved witness.
