# Copilot Plan P1: final remediation and deployment

The backend now distinguishes calculated stop candidates from a user-stated held stop, refuses
unsupported stock playbooks and incomplete risk inputs, and rejects model-authored price
predictions. This completes the C1–C5 review and the R-C2/R-C3/R-C4 follow-up. The user's
October 15 instruction authorizes finishing the fixes, documentation, tracker and deployment.

## Final behavior

| Finding | Resolution |
| --- | --- |
| C1: stop could fall while claiming to be maintained | Stateless candidates are labeled explicitly; without a typed stop, the actual stop is unknown. With `stop=`, proposals keep or raise it and report the daily-close condition. The deterministic endpoint includes the typed stop in its payload. |
| C2 / R-C2: options received stock rules | Explicit unsupported state suppresses entry, sizing and holding rules, including fallback. Possession wording includes “have”; ambiguous long/LONG calls are treated as options, while explicit platform calls remain available. |
| C3 / R-C3: partial or conflicting amounts | Whole numeric tokens and units are validated. k/m suffixes are supported. Percentages in both risk and “can lose” paths require account size; percentage stops are refused as absolute prices. Conflicting budgets/account sizes are refused. Repeated identical budgets remain valid. |
| C4 / R-C4: unrelated conditions/negation excused predictions | Conditions come from code-rendered rules. Only bounded denials of a forecast are exempt. “No doubt … will rise” and unrelated ownership negation are refused through checker, retry and fallback. Repair instructions cite rule keys rather than suggesting model-authored conditions. |
| C5: loss exit called profit | Targets are compared with the typed entry; at/below-cost levels are described as breakeven or loss reduction. |

Packet v5 records the changed stop semantics; existing saved messages retain their original
packet version. No schema migration is needed. No account/broker state is inferred. The API
remains authenticated, with no model call for the deterministic plan endpoint.

## Validation

- Full research-engine suite on SQLite: **692 passed, 8 PostgreSQL-only skipped**.
- Frontend: **588 passed**, TypeScript typecheck passed; tracker labels/colors included.
- The expanded plan probe tests the real packet, sizing, held-stop rendering, options/fallback
  rules, and service repair followed by deterministic fallback. No paid model calls were made.
- The first full run exposed an existing fixture-clock defect: October 9 bars were combined
  with wall-clock requests, so fresh-data draft checks expired after October 14. Request,
  claim and explicit test clocks now use the fixture's fixed date. Production staleness rules
  were not relaxed. The subsequent full SQLite run passed.
- One new sizing assertion initially omitted the account-value cap. It was corrected to
  expect 500 shares capped by a $50,000 account, while preserving the $5,000 risk budget.
- Full research-engine suite on disposable PostgreSQL 15: **700 passed**.
- Final expanded plan suite: **71 passed on SQLite and 71 passed on PostgreSQL**, including
  the last malformed-token regression. All temporary PostgreSQL containers were removed.

Historical independent review evidence is retained under
`evidence/2026-10-15-copilot-plan-rereview/`; those hashes/results describe the earlier reviewed
source, not the final implementation. The original reports remain historical records.

## Deployment scope and open work

Tracker tiers **444** (copilot Plan remediation) and **445** (price-risk shadow audit fixes)
separate completed engineering from remaining evidence. The stock-page Game Plan rebuild,
chart overlays and user-confirmed stop/target alert drafts remain P2 work. The October 23
shadow review, MU-02 earnings-stage verification and broker-fill multi-cycle evidence are
still open. Price-risk delivery and user enrollment are unchanged.

Preflight: **40 GB free, 3.1% iowait**. All twelve running backends were compared with the
working tree: only research-engine differed. The shared calendar already matched everywhere,
so no extra backend rebuild is necessary. Production checkout was clean at `d8aa9b33`.

Deploy research-engine and frontend sequentially through `scripts/deploy.sh`, while holding
the host's `scripts/deploy_lock.sh` lock for the whole operation. Verify health, all twelve
backend hashes, served tracker entries, route authentication and the deterministic plan against
the production database without sending alerts or invoking a paid model.

## Production verification — 2026-10-15

Release **`89756b83`** was pushed to `prod`. Research-engine and frontend were rebuilt and
recreated sequentially under the host deployment lock; no session-only file copies were used.
The frontend image is `a04da63e8cb7`. Served tracker bundle contains both new item IDs for
**tiers 444 and 445**, and `/improvements` responds successfully.

A deterministic read-only smoke in the running research-engine container used production
stock data and the real plan service. Packet v5 and the typed-stop payload round-trip passed;
options requests exposed zero stock rules; the audited forecast assertion was refused; `$10k`
remained 10,000 and malformed `$1.2.3k` produced no risk budget. No model call, conversation,
draft, order or alert was created. The unauthenticated gateway plan route returns **401**.

All **15 containers are healthy**. Redis confirms the price-risk delivery flag is absent;
this release did not change preferences or enrollment. The post-fix shadow cohort remains
October 16, 19, 20, 21 and 22, with review Friday October 23. MU-02 and broker-fill checks
remain occurrence-gated and open.

The deploy health gate confirms both rebuilt services healthy and every declared service
present. The final source sweep reports **0/12 backend drift**. Host checks after the health
settle: **38 GB free, 5.7% iowait**. The frontend is verified by its served bundle, rather than
counted as a backend source tree. Dangling-image cleanup runs under the same host lock.

The locked deploy completed successfully (exit 0). Cleanup reclaimed 2.456 GB and restored
40 GB free. The final documentation-only commit records these observations; application
images remain the source built from `89756b83`.
