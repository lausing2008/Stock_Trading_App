# MU covered-call selection evidence

[Report](../../2026-10-24-mu-covered-call-selection.md).

- `archive_probe.py` / `archive-probe.json`: read-only MU November 20 sample, latest archive session October 22. Time limit 15 seconds; at most 25 selected public-market contract rows; no user positions or credentials.
- `cboe_probe.py` / `cboe-probe.json`: independent unauthenticated public delayed-chain presence check. Both near-price and distant exact identifiers exist at the recorded check. The timestamp is preserved as reported without inventing its timezone. No production feed integration or Fidelity account check.
- Regression tests use synthetic fixtures; no claim of best expected return. Rollout and final verification are appended on completion.

The public Cboe JSON endpoint is a one-off diagnostic source. Re-running it produces a new point-in-time result and must not overwrite this saved witness.
