# Covered-call candidate comparison validation

All new fixtures are synthetic. No new production quotes, credentials, user holdings or trading instructions are published by these tests. This implementation is local and not deployed.

`test_covered_call_candidates.py` exercises actual strategy code, including unusable target candidates, quote/contract/payoff exclusions, exact spread boundaries, missing Greeks, expired evidence and deterministic alternatives. Its route test executes real handler/composition source with deterministic time scope and stubbed DB/provider dependencies: a response with only alternatives stays available.

The UI tests exercise explicit comparison choice, revised payoff/Greeks, renewed assignment acceptance, stale snapshot refusal, fresh-response invalidation and older-response compatibility.

Guard removal runs mutated modules in isolated subprocesses; the working source is never modified. Removing the spread screen fails two cases; removing the same-day-expiry guard fails one. To repeat from the repository root:

```sh
cd services/market-data
python ../../docs/audits/evidence/2026-10-23-covered-call-candidates/check-candidate-guards.py
```

Validation totals are recorded in `validation.json` after the full suites finish. No interactive browser check or production build/deployment is claimed for this follow-up.
