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

---

## AUD-A17-SETTLECOUNTER (found 2026-09-17, by the external production-verification audit)

The companion to the bug above. That one was **the runner** unable to report a failure; this one
is **the assertion** unable to detect one. Same consequence: a green signal over broken code.

**The bug.** `settle_expired_positions` in `options_income_engine.py`:

```python
settled = 0                                              # the integer this returns
for pos in open_positions:
    settled = _settlement_close(session, pos.stock_id, pos.expiry)   # a (price, date) TUPLE
    ...
    settled += 1        # TypeError: can only concatenate tuple (not "int") to tuple
```

Introduced 2026-09-16 by the T400 fix for A05, which replaced a lenient backward-window lookup
with an exact-session one and reused the counter's name for its result. It raised on the **first
position that actually settled**, every run. Two further paths were broken: the counter reset
each iteration (so a multi-position batch would have miscounted even without the raise), and the
missing-price path returned `None` from a function annotated `-> int`.

**Why it was worse than a counter bug.** The exception fires *after* the mutations:

```
portfolio.current_cash += econ["cash_released"]   # done
pos.stage = "closed"                              # done
...
settled += 1                                      # <-- raises here
```

`run_options_income_step` catches it and only logs. It then proceeds to
`_snapshot_income_equity_curve`, whose `session.commit()` is **unconditional**. So a settlement
this function reported as *failed* would have been committed anyway, by a different function,
several steps later. The fix wraps the batch in `try/except` that calls `session.rollback()` and
re-raises — settlement is now all-or-nothing.

**Why the test did not catch it.** It asserted on source text:

```python
body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def settle_expired_positions"):]
assert "if settled is None:" in body        # a check on a local VARIABLE NAME
```

This passed against code that raised on every run, and then **failed when the variable was
correctly renamed**. Failing for a safe rename while passing for a real defect is exactly
backwards.

The file's own docstring states the policy that produced it: the DB-facing functions are "thin
glue… nothing meaningful to assert against a MagicMock session". A17 disproves it — a ~40-line
fake session (`execute().scalars().all()` returning a list, plus `commit`/`rollback` counters)
drives the real function end to end. Seven behavioural tests now cover zero/one/multiple expired
positions, mixed available and missing session closes, an injected mid-batch failure, and cash
conservation.

**Sabotage-verified in both directions**, as the lesson below requires: reintroducing the name
collision fails 6 of 7; removing the `session.rollback()` fails exactly 1; restoring the fix
returns all 7 to green.

**One source-text assertion was kept deliberately** — that the function calls `_settlement_close`
and never `_chain_as_of_on_or_before`. That pins a *choice of function*, which is a real design
constraint, rather than a variable name. That is where the line belongs: text assertions are for
"this must not call that", never for behaviour a fake session can exercise.

### The lesson, extended

A test is a signal only if **both** halves hold: the runner must propagate a failure (above),
**and the assertion must be able to tell the defect apart from the fix** (here). A test that
cannot go red for the bug it names is decoration.

The check is the same in both cases and costs under a minute: break the code on purpose and
confirm the test goes red.

---

## AUD-A19-DEPLOYMASK (2026-09-17) — a deploy script reported success over a live API outage

The third instance of this bug class in two days, and the worst, because it was **self-inflicted
one day after writing the lesson above**.

**What happened.** While rebuilding all 12 backend images (AUD-A15), an ad-hoc inline loop did:

```bash
docker compose up -d --force-recreate "$svc" >/dev/null 2>&1
for i in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' stockai-$svc-1)" = "healthy" ] && break
  sleep 3
done
echo "$svc: $(docker inspect -f '{{.State.Health.Status}}' stockai-$svc-1)"
```

`api-gateway` declares `depends_on: market-data: condition: service_healthy`. At that moment
market-data was briefly **unhealthy** — several services start by issuing
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` and concurrent starts deadlock on it:

```
Process 25617 waits for AccessExclusiveLock on relation 33181; blocked by process 25559.
Process 25559 waits for AccessShareLock  on relation 33167; blocked by process 25617.
```

So compose **created** the api-gateway container and refused to **start** it, printing an error
— into `/dev/null`. The health poll then inspected a container that had no Health key at all,
and `docker inspect` emitted a template error rather than a status. The loop moved on. The
script wrote `EXIT=0`.

**Result: the API was down for ~10 minutes while the deploy reported success.** Static pages
still returned 200 from Next.js, so the site *looked* fine; `/api/health` returned 500.

**Three separate masking mistakes, each sufficient on its own:**

1. `>/dev/null 2>&1` discarded the one message that explained everything.
2. The health check could not distinguish *unhealthy* from *no health state at all* — and
   `Created` is the latter, so the worst outcome read as the least alarming.
3. The loop's bounded wait expired into "continue anyway" rather than "stop and report".

**Fix:** `scripts/rebuild_backend_images.sh`. Never discards output on failure; requires BOTH
`running` and `healthy`, treating a missing Health key as failure; stops at the first failure
rather than compounding it across 12 interdependent services; exits non-zero naming what broke.
Verified in both directions — exit 1 with a service that cannot build, exit 0 on a clean run.

**A postscript, because it is the same mistake again.** The first attempt to verify that exit
code did `bash rebuild.sh no-such-service 2>&1 | tail -6; echo "exit=$?"` and read `exit=0` —
`$?` after a pipeline is **tail's** status, not the script's. The script had been correct all
along; the *measurement* was wrong. Re-run without the pipe: exit 1, as designed.

### The lesson, third time

Each instance has been one layer further out: the test runner (`make test`), the assertion
(`settled is None`), and now the deploy script. All three shared one shape — **something that
could only ever report success**. The check is always the same and always cheap: make it fail on
purpose and confirm you can see it. And when you do check, make sure you are reading the exit
code of the thing you are testing, not of the last command in your pipe.
