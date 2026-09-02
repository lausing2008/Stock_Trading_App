## Recurring Issue: Stale Tracker Entries Can Point Either Direction — Verify Before Trusting Severity/Status

**Found 2026-07-16, while looking for "the next critical improvement to build."** A tracker
survey flagged `SE-F2-SAME-DAY-CLOSE-LOOKAHEAD` (tier 147, `severity: 'critical'`, no
`defaultStatus` field, `implementedNote: 'Deferred'`) as the top candidate — signal outcome
evaluation allegedly still used the same-day close as entry price, corrupting every
accuracy/calibration metric. Before building anything, checked the actual code first: the fix
had already shipped 2026-06-30 (`services/signal-engine/src/api/routes.py:5056-5059`,
explicit "T+1 entry... avoid same-day look-ahead bias" comment) as part of an unrelated
broader audit commit, and was confirmed byte-identical between the local checkout and the
live production container. The tracker entry itself was simply never updated to reflect it.

**This is the mirror image of the T203 incident** documented earlier in this file (T203 was
marked `done` but was actually never wired up/functional) — here, the entry was marked
effectively `todo`/deferred but the fix was actually live. **Both directions of staleness are
real and both have occurred in this tracker** — a tracker entry's `severity`/`defaultStatus`
tags are a starting hint for where to look, never a substitute for reading the actual current
code before deciding what to build or report as still-broken.

**A second real issue was found underneath the stale tracker entry**: 3,808
`signal_outcomes` rows (`signal_date < 2026-06-30`) still carried the pre-fix same-day-close
bias and were still feeding the self-tuning watchdog/calibration thresholds even after the
code fix landed — the code fix only affects evaluation going forward, it does not retroactively
correct already-written rows. Fixed by backing up the 3,808 rows to
`signal_outcomes_prefix_backup_20260716`, deleting them from the live table (explicit user
confirmation obtained naming the specific table before the DELETE), and re-running
`POST /signals/outcomes/evaluate` to regenerate them with the corrected T+1 entry price —
verified `COUNT(*) FILTER (WHERE entry_date = signal_date) = 0` across all 4,742 resulting
rows, and spot-checked several regenerated rows against their pre-fix backups to confirm
materially different (and correct) entry prices.

**Design invariant**: a code fix for a data-integrity bug (lookahead bias, wrong formula,
etc.) fixes future writes only — always check whether historical rows written before the fix
need a separate backfill/re-evaluation pass, and don't assume "the code is fixed" means "the
data is fixed." When surveying this tracker for "what's the next critical thing," always
verify a candidate's actual current code state directly before trusting its severity/status
tags in either direction — an entry can be wrong by claiming something is still broken
(costing you nothing but a wasted verification pass) or by claiming something is fixed when
it silently isn't (costing real time debugging a "mysterious" recurrence of an already-known
bug). Verify first, in both directions.

---


## Recurring Issue: Stale Tracker Entry — T171-RETURN-TARGET-ANALYSIS Was Already Fully Done

**Found 2026-07-22, while surveying the tracker for "next improvements."** A survey agent
flagged `T171-RETURN-TARGET-ANALYSIS` as the top open candidate — its `what`/`fix` text
described insider/congress scores as "metadata only," no premarket gap filter, no scale-out
exit logic, and risk_off regime only dampening size rather than blocking entries. Before
building anything, verified each of the 5 named gaps directly against current code and found
**all 5 were already independently shipped**, each under a DIFFERENT tracker id, none of which
cross-referenced back to close this entry out:
- insider/congress → `fused_prob` wiring: `T172-CATALYST-INTO-FUSED-PROB` (done 2026-06-24,
  `services/signal-engine/src/api/routes.py` `_bulk_persist()`), plus a `T237-EI1` fix for
  negative congress scores.
- options_flag → `fused` adjustment: already live in `signals.py`'s `_apply_style_signal()`
  (lines ~2027-2042) — this sub-claim was stale even at the tracker's OWN original analysis
  date, not just now.
- Premarket gap filter: `paper_trading_engine.py`'s `_should_enter()`, `max_entry_gap_pct`
  (0.04 default), explicitly tagged `# T171` in its own comment.
- Scale-out exits: `T232-PT6`'s two-level scale-out (sell 50% of remainder at +12%, move stop
  to +5%).
- Strict risk_off gating: `T226-A` (2026-06-30) — `regime_risk_off_gate` defaults to `True`
  (blocks ALL new entries in risk_off), not just size dampening, based on a real 9-trade
  0%-win-rate audit finding.

**This is the mirror image of the SE-F2/aud14 staleness pattern already documented elsewhere
in this file** (an entry claiming something is BROKEN when it's actually fixed, rather than
claiming something is fixed when it's actually broken) — both directions are real in this
tracker, and both require reading the actual current code before either building or reporting
status, never trusting a tracker entry's own `defaultStatus`/`what`/`fix` text at face value.

**Fix applied**: flipped `T171-RETURN-TARGET-ANALYSIS`'s `defaultStatus` to `'done'` with an
`implementedNote` cross-referencing all 5 closing tracker ids, so a future survey doesn't
re-flag this same already-closed gap.

**Design invariant reinforced**: whenever a broader initiative's individual sub-items get
closed under their OWN separate, narrower tracker ids (a common pattern in this tracker — see
`T232-DL-DUALSCORER-DEBT`'s own many dated `UPDATE` notes for the opposite, correctly-
cross-referenced version of this same pattern), the ORIGINAL broader item must be updated too,
or it becomes a standing false-positive for every future "what's still open" survey. When an
entry names several sub-gaps, check each one against current code individually before trusting
the entry's own status — a broader item can be entirely stale even when its constituent fixes
were each done correctly and are individually well-documented elsewhere.

---


## Recurring Issue: T230-NEWS-REALTIME — Stale Tracker Entry, Same Staleness Pattern As SE-F2/aud14 (Corrected 2026-08-01)

**Found via a routine "next improvements" survey** — the tracker entry claimed real-time news
was still `todo`, deferred because it "requires a paid real-time news subscription (Benzinga
Pro ~$40/mo or Polygon.io). Not feasible without upgrading data sources." Verified directly
against current code before trusting that claim (per this file's own standing "verify tracker
status in both directions" discipline) and found it false: `T259-NEWS-INTELLIGENCE` (2026-07-27,
already documented at length elsewhere in this file) built a full standalone
`news-intelligence` service (port 8011) with a genuine PUSH-based real-time source — an Alpaca
news WebSocket (`wss://stream.data.alpaca.markets/v1beta1/news`,
`services/news-intelligence/src/services/alpaca_source.py`) with auto-reconnect, plus 3 polled
sources (PR Newswire, Business Wire, SEC EDGAR real-time filings) on 1-2 minute cycles — all
materially faster than the 30-60 minute yfinance/Google-News staleness this entry's own `what`
text described. The real gap was already closed under a DIFFERENT tracker ID with no
cross-reference back to this one — the exact SE-F2/aud14 staleness pattern already documented
multiple times in this file, just recurring on a new item.

**Fix applied**: flipped `T230-NEWS-REALTIME`'s `defaultStatus` to `'done'` with an
`implementedNote` cross-referencing `T259-NEWS-INTELLIGENCE` and noting the one real
difference from the original ask — Alpaca's free-tier news WebSocket was used instead of the
named paid Benzinga/Polygon subscription, achieving the same real-time-push goal without the
paid cost.

**Design invariant reinforced (yet again)**: whenever a broader ask gets closed under a
DIFFERENT, later tracker id (a common pattern in this tracker), the ORIGINAL item must be
updated too, or it stays a standing false-positive for every future "what's still open" survey.
Always verify a `todo`-tagged item's actual current-code status before either building it fresh
or reporting it as a real gap — this is now a repeatedly-recurring category of finding in this
tracker's own history, not a one-off.

---

