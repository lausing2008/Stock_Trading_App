# CI Failure Masking — a green build over a red test suite

## AUD-T400-CIHIDESFAILURE (found 2026-09-17, by the external system audit's A13)

**Symptom.** None. That is the entire problem: CI was green, and had been green over a failing
backend test suite for **more than three months**.

**The bug.** `make test` looped over the 12 services like this:

```make
for svc in ...; do
    (cd services/$$svc && python -m pytest -q; ec=$$?; [ "$$ec" -eq 0 ] || [ "$$ec" -eq 5 ] || exit 1); \
done
```

`exit 1` runs inside the per-service **subshell** `( … )`, so it exits only that subshell. The
`for` loop keeps going, and **a shell loop returns the exit status of its last iteration**. A
failure in any service except the final one was therefore discarded entirely, and `make test`
exited 0. The GitHub workflow's `- name: Run unit tests / run: make test` step consumed that 0.

Reproduced standalone before fixing, rather than reasoned about:

```bash
for svc in fails passes; do
  ( if [ "$svc" = "fails" ]; then ec=1; else ec=0; fi; [ "$ec" -eq 0 ] || exit 1 )
done
echo "LOOP EXIT STATUS: $?"   # -> 0
```

**What it was hiding.** Three real `research-engine` failures in `tests/test_scoring.py`, dating
to **2026-06-10** (commit `c36fb02`). Both breakages were *deliberate improvements* whose tests
were never updated:

| Deliberate change | Why the old test broke |
|---|---|
| D/E denominator corrected from `debt/cash` to `debt/(book_value x shares_outstanding)` | tests passed only `total_cash`/`total_debt`, which can no longer produce a ratio — the assessment correctly became `Unknown` |
| missing-data default score lowered 50 -> 35 ("uncertain, not neutral") | test still asserted 50 |

So the *code* was right and the *tests* encoded superseded behaviour. They were corrected to
assert current behaviour, given real inputs so they exercise the thresholds they were always
meant to, plus new coverage for the `Unknown` and `Average` cases that previously had none.

**Fix.** Run every service, record whether anything failed, exit non-zero at the end. Running
all of them rather than failing fast is deliberate: with 12 suites, seeing every failure in one
run beats discovering them one restart at a time. pytest exit code 5 ("no tests collected")
remains a legitimate pass.

**Verified in both directions** — with the stale tests still broken it exited **2** and printed
`!! research-engine FAILED`; after fixing them it exited **0** with `all services passed`.

**A side finding.** The audit reported `api-gateway`, `decision-engine` and `event-intelligence`
as *timing out at 240s, incomplete*, and was careful to say that was "not proof of a service
outage". Running them through the fixed target, all three completed in **2-4 seconds each**
(54, 387 and 451 tests). The timeouts were an artefact of that audit's own three-at-a-time
concurrent harness, not a property of those services.

## The lesson

A test suite is only a signal if a failure can actually reach you. Two independent things have
to hold: the tests must fail when the code is wrong, **and** the runner must propagate that
failure. This repo already invests heavily in the first and had a silent hole in the second.

When touching any aggregate runner (`make`, a shell loop, a CI matrix, a `for` over services),
verify it FAILS on a deliberately broken member — the same sabotage discipline already applied
to individual tests elsewhere in this codebase. A runner that has never been observed to go red
has not been shown to work.
