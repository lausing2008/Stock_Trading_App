# Follow-up audit remediation — R01–R10, all ten closed

Remediates every finding in
[`2026-09-24-fix-verification-and-followup-audit.md`](2026-09-24-fix-verification-and-followup-audit.md),
the external review of the twelve DA-xx fixes recorded in
[`2026-09-24-deep-audit-remediation.md`](2026-09-24-deep-audit-remediation.md).

Commits, in order: `e5486b8` (U01/U02/W01), `bd3402a` (R02 part 1), `8b51443` (R01 part 1),
`4260d33` (R04), `ab9e139` (R05), `9156fda` (R07), `85a1161` (R09+R10), `4b34fb8` (R06),
`430a2c7` (R08), `47444e1` (R02 part 2), `6d70a1b` (R01 part 2), `f191269` (R03).

## The pattern the audit was really about

Eight of the ten findings are the same shape, and naming it is worth more than the individual
fixes: **a defect was detected, recorded, and then not acted on.** The audit's own sentence for
it is "Logging leakage is not a restriction on using it."

| Finding | What was recorded | What still happened |
|---|---|---|
| R02 | `embargo_shortfall` in metrics | the artifact was used as if the split were clean |
| R02 | `threshold_holdout_too_small` log | the in-sample metrics were reported as OOS |
| R05 | the flag's `ts` | an older story overwrote a newer unresolved event |
| R06 | `lock_not_owned_at_release` | the losing worker kept writing |
| R08 | mark source counts in a log line | the log rotates; the equity row does not explain itself |
| R09 | the blend flag | meta still ran and was still named as a contributor |

A second shape appears three times — **a guard placed on one route while another route went
around it**: R05 (the clear path was guarded, the overwrite path was not), R07 (ordinary users
were denied, the disabled-admin path was not), R01 (dates were deduplicated, availability was
not checked).

## The findings

### R05 — a positive story erased a risk brake by overwriting it
DA-09 guarded the *clear* path. Every material non-macro story called `_mark_hot()` directly,
which `setex`'d over whatever was stored — so a positive article removed an unresolved negative
flag just as effectively, without touching the guard. Two more defects in the same function:
`ts` was INGESTION time compared against the follow-up's PUBLICATION time (ingestion trails
publication, so a real correction was rejected as "older"), and the read/check/delete was not
atomic. Both Lua scripts were exercised against the production Redis (13 cases) before writing
the test fake that reimplements them.

**Still open, pinned by a test:** an unrelated newer positive story can still clear an
unresolved event, because nothing links a story to the event it claims to resolve.

### R06 — a correct lease release does not stop the work
DA-07 stopped run A deleting run B's lock; it did nothing about run A's *work*. Three guards,
and the Redis one is the weakest: a self-renewing lease (a transient Redis error does **not**
mark it lost — a blip is not proof, and the TTL is the backstop), an abort-before-commit, and
**the database** — a `FOR UPDATE` row lock plus a unique intent key on
`(portfolio_id, option_symbol, entry_date)`. Only the last survives a worker being paused or
killed, because neither state is observable from Redis.

### R07 — a disabled administrator's token still authorized model changes
Two layers, because neither does the other's job. **Live account state** on privileged routes
(the only thing that catches role *demotion*, and it needs no Redis; fails closed with 503).
**Per-user token revocation** on disable/delete/password-reset, keyed on a new `iat` claim
(one Redis read, fails *open* on an unreadable marker — deliberately, because the database
layer is what guards the expensive routes). `svc` gained named scopes, so a leaked
risk-snapshot or signal-engine token can no longer retrain anything.

**Still open, pinned by a test:** a self-service password change does not revoke the user's
other sessions; doing so would log them out of the session they are in, and there is no
token-refresh path on the frontend.

### R08 — a mark's age was known to the query and discarded
`_MAX_ASK_AGE_DAYS = 5` was doing two incompatible jobs: bounding what may be *read*, and
implying anything read is a good *mark*. A four-day-old $0.01 ask on an OTM put became a $1
liability labelled `quote_ask`; the intrinsic floor binds nothing when intrinsic is zero, so
stale time value passed straight through. Now graded (fresh/recent/stale/floored/intrinsic),
with the underlying's own provenance (live / archived close / entry-price fallback, gated on
the snapshot actually being today), persisted in a `mark_evidence` column.

**It grades, it does not revalue** — a test asserts a stale mark produces identical equity.

### R09 / R10 — provenance
R09: the DA-02 flag gated only the arithmetic; meta still ran and was still reported as an
ensemble member. Resolved once at the top; shadow mode is separate and labelled.
R10: `m = dict(sleeve)` carried `max_drawdown` unrecomputed. The fix is not "also recompute
drawdown" — copy-and-patch is the defect, since every metric added later inherits the wrong
one. `_metrics` is now called twice and nothing is copied.

### R01 / R02 / R03 — the ML dataset and split contract
R01: outcome rows were admitted by *date*, not by when their label became knowable; and — the
larger half — they were built with `macro_df=None` and none of the main path's seven inputs, so
every column those produce was **zero-filled** at double weight, and labelled with
`SignalOutcome.is_correct` rather than the base forward-return target.
R02: preserving ten rows per slice conflicts with calibration (needs 20) and honest threshold
reporting (needs 10 after halving). Status is now recorded and acted on, and an invalid
candidate no longer publishes over a valid incumbent.
R03: a row split divided sessions (the guard rejected `>`, never `==`); records carried no
label availability; and one slice both stopped the fit and decided promotion, against the
incumbent's *historical* AUC from a different cohort.

## Measurements taken before shipping

* **R02's fleet impact.** Of 1,297 loadable artifacts, 1,167 (90%) were already suppressed. Of
  the 130 that were not, 126 clear the ten-row reporting minimum — only **4** are newly
  suppressed. Calibration was already fitted for 121 of those 130, which is why its status is
  recorded but does **not** cost evaluation validity.
* **R06's index.** 0 duplicate `(portfolio_id, option_symbol, entry_date)` rows in production,
  so the unique index creates cleanly. 9 options-income positions open.
* **R05's Lua.** 13 cases against the production Redis, on a scratch key.

## Verification

**70 sabotages** across the nine findings closed in this pass (R05 6, R07 9, R09 7, R10 5,
R06 10, R08 8, R02 8, R01 6, R03 11), every one confirmed red and then restored green. R04 and
the first halves of R01/R02 were verified in their own earlier commits and are not counted
here. The full 12-suite gate was run green before each commit.

Four things that went wrong and are worth carrying forward:

1. **A sabotage loop that ran zero tests.** The R08 loop named a test file that does not exist,
   so pytest reported "no tests ran" eight times and I read it as eight passes. A runner that
   cannot go red is not a check — print the baseline before the loop.
2. **A sabotage that hung instead of failing.** Removing R06's visited-set guard makes the
   exception-chain walk loop forever. That test now runs on a worker thread with a join timeout.
3. **Prose collisions, four times** (R04, R05, the T398 ordering test, R01). A comment or
   docstring that legitimately names the function a positional assertion searches for makes the
   assertion measure the *prose*. Strip comments and docstrings, or use the AST.
4. **A mirror that pinned itself.** R03's first test reimplemented the split rule and then
   pinned the shipped lines with source-text assertions to keep them in step — DA-11's own
   failure mode, and it pushed the AUD-T401 ratchet 181 → 184. The helper now *executes* the
   extracted block. Then the docstring explaining this failed the ratchet by quoting the
   anti-pattern; it now describes it. Ratchet back to 181.

## Deliberately not done

* The **meta blend stays disabled** (DA-02/R09). R03 is about whether the evidence could
  support re-enabling it, not about enabling it.
* **Event-linked news resolution** (R05's remaining gap) needs an event table with ids,
  materiality, supersession and expiry — a schema, not another guard.
* **Self-service password change does not revoke** (R07's remaining gap) — needs a
  token-refresh path on the frontend.
* **Nothing is deployed yet.** `shared/db/models.py` and `shared/common/jwt_auth.py` both
  changed, so this needs all 12 backends rebuilt, plus two idempotent DDL statements
  (`uq_options_income_intent`, `mark_evidence`) that `create_all()` will not apply on its own.
