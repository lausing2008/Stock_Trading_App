## Recurring Doc Review: 2 Stale Audit/Roadmap Documents — One Held Up, One Didn't (2026-08-16)

**Trigger**: user asked to review `docs/AUDIT_SHORT_SQUEEZE_2026-07-25.md` and
`docs/STRATEGIC_IMPROVEMENT_ROADMAP_2026-07-25.md` (both 3 weeks old at review time) for
trustworthiness before implementing anything — explicit instruction that the roadmap doc "is
only for reference and supplement, not a force to do."

**Method**: 2 parallel research agents, each independently verifying one document's claims
against the actual CURRENT codebase (never trusting the doc's own line numbers or "✅ Verified"
annotations at face value) — matching this repo's own standing discipline that a stale tracker/
audit entry can be wrong in EITHER direction (claiming something broken that's fixed, or
claiming something fine that's broken).

### `AUDIT_SHORT_SQUEEZE_2026-07-25.md` — held up completely

All 6 issues (fundamentals cache-miss counter unmetriced, 30-day stale-short-interest cutoff too
generous, 0-DTE OI staleness only inline text, `check_squeeze_watch_reverts()` had no cache-miss
counter at all, the backtest endpoint couldn't distinguish two different zero-candidate
diagnostic states) and both cheap performance suggestions (MGET pre-warming, calibration bucket
caching) were confirmed STILL genuinely open against current code — zero overlap with anything
shipped in the 3 weeks since (T264-SQUEEZEALERT-PERFORMANCE/PREBREAKOUT/RECOMMENDATIONS-BATCH,
all shipped 2026-08-14/15, only extended existing mechanisms to more alert types, never touched
any of these 6 issues). Appendix A's own line-number citations had drifted moderately (one
function moved ~97 lines from new code inserted ahead of it) but every substantive claim held.
**All 6 issues + both performance items were implemented and deployed same-day** — see the
"Feature Reference: AUD-SQUEEZE250725-BATCH" section immediately below for the full writeup.

### `STRATEGIC_IMPROVEMENT_ROADMAP_2026-07-25.md` — did NOT hold up, correctly set aside

**Foundational problem**: the roadmap's headline "measured performance" numbers (SWING BUY win
rate ~27.5%, SELL ~61.7%) traced to a single stale code comment in `signals.py` (dated
2026-06-18, the "SA-31" comment), not a live query — and this session's own Tier 261 audit
(2026-08-05, 11 days AFTER this roadmap's own date) found the app's actual accuracy-reporting
layer was systematically biased in the loss-hiding direction, then pulled REAL production ground
truth showing **SWING BUY at 37.9%** — 10+ points higher than what the roadmap was optimizing
against.

**At least 4 of its 8 core proposals already existed**, built either before or shortly after the
roadmap's own date, none cited: QW-2 (Entry Timing Score) — a more surgical version already
shipped as `T232-SIG-ENTRYTIMING` TWO DAYS BEFORE the roadmap's own date, then had a real math
bug fixed in it two days after (`BUG-SA33-UNREACHABLETHRESHOLD`); QW-3 (Sector Momentum Filter)
— already live (`SA-16`, `signals.py:2251-2265`); QW-4 (Volume Confirmation) — already live
(`SA-32` VOLUME pillar); MT-4 (Options Flow Integration) — already feeding main signal fusion
directly, not squeeze-alerts-only as the roadmap claimed. The roadmap is also self-contradicting
internally: Part 1.2's own feature table lists per-symbol rolling accuracy as an EXISTING ML
feature, while Part 2.3 lists "no per-symbol model performance tracking" as a gap.

**Most importantly**: MT-1 and MT-2 propose calibrating NEW mechanisms against
`SignalOutcome`/`gate_harness` data that Tiers 262-263 (same 2026-08-05 series) proved is
actively corrupted — unblended scale-out writeback recording winners as losses, and a weekly
weight-mutation job (`calibrate_conviction_weights`) with zero validation gate. Building on top
of that foundation before fixing it would repeat exactly the mistake the audit series exists to
prevent.

**Disposition**: kept as a reference document, per the user's own framing — nothing from it was
implemented. If BUY-signal-quality work is revisited, the correct starting point is the verified
`by_direction` numbers and the already-fixed `SignalOutcome` writeback, not this document's
specific numbers or mechanisms.

---

