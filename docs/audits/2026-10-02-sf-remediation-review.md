# SF-01–SF-05 remediation review

Reviewed October 2, 2026 against `10016977b51a3a1243d97a6ec390d6518bbc8849`.

The fixes improve the original witnesses, but **“all five closed” would overstate the result**. SF-03 still has a reachable recommendation bypass, and SF-05 rejects past dates but still admits malformed ones. Claude correctly leaves the PostgreSQL broker lifecycle acceptance and M25 activation open.

## Independently checked

- Ran `python -m pytest -q --noconftest services/market-data/tests/test_sf01_sf05_followup_audit.py`: **36 passed**. This isolated run bypasses service conftest; it is not a full-suite result.
- Executed [new local probes](evidence/2026-10-02-sf-remediation-review.py), with [captured output](evidence/2026-10-02-sf-remediation-review.json).
- The real extracted broker eligibility/claim functions reject stale selections after **each** of the five eligibility conditions becomes false in an isolated SQLite database. This verifies sequential predicate behavior, not concurrent PostgreSQL lifecycle semantics.
- The original one-share scenario now recommends a long call; the original negative-DTE scenario produces no primary recommendation.
- Inspected shared quote-helper use and frontend provenance rendering code. No browser, production verification, sabotage run or complete application suite was executed in this review.

## Status by finding

| Finding | Review result |
|---|---|
| SF-01 | Missing atomic predicates fixed and sequentially verified. Cancellation after claim, intent validity and concurrent dispatcher/close behavior remain open. |
| SF-02 | Legacy route now calls the matrix's quote helper and guards unavailable results. Route integration tests still need behavioral coverage; the new tests mostly inspect source for this finding. |
| SF-03 | Normal one-share witness fixed; fallback still bypasses coverage. **Not closed.** |
| SF-04 | Last-trade qualifier is present on leg and primary rendering paths. Browser/rendered behavior and fuller per-leg timing remain unverified/open. |
| SF-05 | Past expiry witness fixed. Malformed expiry still accepted; same-day execution eligibility remains a separate session check. |

## Residual 1 — Recommendation fallback ignores share coverage

Location: `services/market-data/src/services/options_strategies.py::_recommend`, final `next(iter(available))` fallback.

The revised share count changes the preference ordering but does not remove ineligible structures from `available`.

**Executed matrix witness:** spot 100, target 105, one share, rich IV, no put chain; the ATM call at strike 100 has crossed prices 12/2, while the target call at strike 105 has valid prices 2/2.2. The long call cannot be built from the selected ATM contract, but the covered call can be priced. No preferred alternative exists, so fallback returns:

```text
primary: covered_call
reason: The only structure that could be priced from the currently-listed chain.
```

The result also omits the coverage count/constraint supplied on the ordinary return path. This is a pricing-versus-eligibility distinction: the only priced structure need not be one this holding can support. No order was placed; this is an advisory defect.

**Required fix:** filter recommendations for eligibility before all ranking and fallback paths. Keep ineligible priced structures in an explicitly illustrative section if useful. When nothing eligible remains, return no primary with a reason. Preserve coverage/constraint metadata on every return path. Do not automatically suggest acquiring extra shares or buying a different option without a separate appropriate plan.

**Acceptance:** execute the full matrix with the fixture above, with zero/partial/sufficient holdings, with sparse and invalid chains, and with combinations present. Assert that neither primary nor alternatives contain an ineligible covered structure. A helper-only `shares // 100` test cannot establish this.

## Residual 2 — Unknown DTE includes invalid expiry strings

Location: `options_strategies.py::_dte` and `_leg`.

`_dte('not-a-date', today)` returns `None`. `_leg` rejects negative DTE but accepts `None` with any nonempty expiry string. The full matrix therefore recommends a long call with `expiry='not-a-date'`, `days_to_expiry=None`.

“Unknown is not expired” is true, but it does not imply “unknown is eligible.” The new test explicitly requiring unknown DTE to build a leg does not distinguish an omitted calculation from an unparseable contract identity.

**Required fix:** validate expiry syntax/contract identity at the construction boundary. Separate missing computation from invalid evidence; do not return a current recommendation for an invalid expiry. Historical/illustrative records may retain raw invalid inputs with an unavailable reason.

Allowing DTE zero in a pricing calculator is reasonable. It must not assert current tradability: last-trading time and expiry date can differ by product, and the execution consumer must check the actual contract/session rules. A pure payoff calculation does not need to decide those rules, but its output should state that eligibility is not established.

## Verification and next steps

Several new tests still assert source strings: `_eligibility()` appearing in a function, quote-helper text, guard text, or UI warning text. These are supplementary structure checks, not behavioral proof. The independent probe now supplies sequential broker behavior; extend the real route tests and rendered frontend tests similarly.

Recommended order:

1. Fix the SF-03 fallback and malformed-expiry residuals with full-builder acceptance tests.
2. Execute the real route with valid, crossed and last-only inputs, asserting returned values and reasons. Verify unavailable reasons actually appear in the UI: the legacy card currently does not consume the new `*_unavailable` fields and can return nothing when no legs are available.
3. Render the last-trade warning in actual component tests/browser checks. Preserve the distinction between last trades, archived quotes and current executable quotes.
4. Run PostgreSQL races through dispatcher and close/cancel paths. If close wins before claim, provider calls must be zero; if claim wins, closure must handle potentially live broker exposure explicitly.
5. Keep M25 disabled until that lifecycle evidence and the separately required activation decision exist. Do not infer deployment or live behavior from a commit or healthy containers.

The historical audit probe should remain unchanged, as Claude proposes. Its first assertion failing shows that one witness changed; because execution stops there, that failure says nothing about the remaining witnesses. Separate acceptance probes are required for each closure claim.

This review changed only documentation/evidence. No application code, production state, flags, trades, emails or `CLAUDE.md` was changed.


---

## Residuals closed — 2026-10-02

Both reproduced from this review's own witnesses before any change, both fixed, both verified
through the FULL BUILDER rather than a helper.

### Residual 1 — eligibility now constrains every path

The first fix changed the preference ORDERING and left ineligible structures in `available`,
so `next(iter(available))` handed them back regardless. Ordering is a hint, and a fallback
does not consult hints.

Eligibility is now filtered **before ranking and before any fallback**, keyed off each
structure's own `requires_shares` flag rather than a list of names, so a structure added later
inherits the rule without anyone remembering. Coverage metadata is carried on every return
path, including the empty ones the fallback previously dropped.

Verified on this review's exact fixture — spot 100, target 105, rich IV, no put chain, ATM
call crossed 12/2, target call valid 2/2.2:

| shares | primary | `ineligible_for_holding` | `coverable_contracts` |
|---:|---|---|---:|
| 0, 1, 99 | **None** | `['covered_call']` | 0 |
| 100 | `covered_call` | — | 1 |
| 200 | `covered_call` | — | 2 |

An ineligible-but-priced structure is reported rather than silently dropped: "we found
nothing" and "we found something you cannot use" lead to different next steps.

### Residual 2 — invalid identity separated from unknown identity

Expiry syntax is validated at the construction boundary, so an unparseable contract never
becomes a priced leg whatever the caller computed for `dte`. `not-a-date`, `2026-13-45`,
`20261106`, `Nov 6 2026` and `""` all now yield no structures and no recommendation.

The distinction this review named is now its own test: `dte=None` with a VALID expiry still
builds (a calculation nobody performed), while `dte=None` with an unparseable one is refused
(an identity that cannot be read). The earlier test could not tell those apart, which is why
it passed over the defect.

**DTE 0 remains available for analysis and asserts nothing about tradability** — last-trading
time and expiry date differ by product, and that belongs to the execution consumer.

### Also fixed — raised by this review in passing

The legacy card consumed neither `protective_put_unavailable` nor `covered_call_unavailable`
and could still render nothing. The SF-02 fix would then have replaced a wrong number with a
blank space, and a reader cannot tell "no such contract" from "the quote is unusable right
now". The card now renders a `quote unusable` indicator carrying the reason.

### Test posture

`services/market-data/tests/test_sf01_sf05_followup_audit.py` is now 57 tests. The residual
coverage executes the full builder; the point that a helper-only `shares // 100` test cannot
establish the property is accepted and was the reason both residuals survived the first pass.
Three sabotage cycles on these fixes: eligibility filter removed (fallback leaks again),
expiry validation removed, and an over-correcting filter that rejects a sufficient holding —
all three caught.

### Unchanged

SF-01's predicate fix is verified; its lifecycle acceptance is not. **M25 stays disabled**
pending PostgreSQL dispatcher/cancellation races, intent-expiry and price-drift controls.
Remaining order as recommended: broker lifecycle verification, then UW failures and premarket
ingestion from the new runtime counters, then inspect current recovery-marker state before
considering any reset.
