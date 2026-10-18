# Next copilot and stock-workspace improvements

Prepared October 17, 2026, from local source and release records through `c6ead3fc`.
Status: all five items are committed and deployed in release `7afcf622`. See the
[implementation and validation record](2026-10-18-workspace-workflows.md). Production verification completed October 18, 2026. GitHub was not pushed in this work.

The earlier C1–C5 and follow-up fixes are recorded as completed in the
[final plan remediation](../audits/2026-10-15-copilot-plan-final-remediation.md).
The [dual Game Plans](2026-10-16-dual-game-plan.md) and
[workspace controls](2026-10-17-stock-workspace-followups.md) are also recorded as deployed.
Those records are not a new independent production verification.

## 1. Establish a chart state and command interface

This is the next implementation slice in the original copilot roadmap. Begin with chart
reliability and preserve the current manual controls before connecting model proposals.

`frontend/src/components/PriceChart.tsx` still owns its controls locally and calls
`chart.timeScale().fitContent()` when rebuilding. A controlled state boundary should allow
supported indicator changes without losing the user's visible range. Explicit range changes
and symbol changes need their own documented reset policy.

First deliverable: move the relevant state behind a small typed interface and retain range
and drawings across ordinary indicator toggles. Then add indicator commands, followed by
timeframe and visible-range commands. Validate that a requested timeframe actually has bars;
do not relabel daily data as intraday.

Acceptance: manual and copilot controls produce the same state; the chart acknowledges a
command before the assistant claims success; undo restores the prior state; unsupported
commands are refused; a late response cannot change another symbol's chart. Exercise pan/zoom,
widget hide/restore, responsive resizing, drawings and symbol navigation in browser tests.

## 2. Make monitoring match the plan's condition

Game Plans express daily-close entry/exit rules, while the existing price-alert drafts monitor
an observed price against a fixed threshold. The current disclosure is useful, but a dedicated
daily-close rule would remove the need for users to interpret an intraday notification as a
close-confirmed event.

Design a separate, explicitly selected daily-close alert type. Define exchange, session,
holiday handling, completed-bar availability, price basis and treatment of a supplied held
stop. Keep candidate levels visibly distinct from the stop a user says they hold. If the user
wants to change an existing alert's fixed level, preview that change and confirm it.

Acceptance: an intraday crossing alone cannot trigger a close-confirmed alert; missing or
stale bars never count as confirmation; retries deliver one event; exchange holidays and
after-hours data are covered. Reuse existing preferences and delivery receipts. A plan refresh
must not silently change an alert, stop, enrollment or delivery flag.

The existing price-risk shadow review remains scheduled for October 23 in the deployment
records. Review its measured volume, missing events and duplicates before changing its live
delivery settings; this roadmap does not authorize activation.

## 3. Measure the LLM alternative on the current contract

The comparison flow now selects a validated setup from the same deterministic snapshot.
The dual-plan document says its implementation checks used scripted providers; earlier model
evaluations do not establish quality on the newer setup-selection contract.

Prepare a fresh, bounded evaluation covering entry, holding, explicit held stops, unavailable
inputs, unsupported instruments, blocked setups and selection of wait. Separate agreement
with the deterministic plan from correctness: the model is interpreting the same evidence,
not supplying independent corroboration.

Acceptance: selected setup IDs are valid, all cited values resolve, refusals stay visible,
and unsupported predictions are rejected. Report first-draft acceptance, repair/fallback,
semantic errors, latency and cost. Use the existing admission/usage records and record
additional outcome fields if needed; the current usage log alone should not be assumed to
measure every quality metric. Do not launch new paid evaluations merely to prepare this work.

## 4. Pilot Tenji-style event connections with reviewed evidence

Start with one sector and a small reviewed supplier/customer catalogue, using existing news,
issuer documents, reports and notifications. Produce a research card with the source event,
affected companies, direct facts, inferred links, counterargument, observable falsifier and
time horizon. Every relationship needs its own dated evidence.

Acceptance: no unsupported causal hop is published as fact; missing exposure stays unknown;
duplicate coverage resolves to one event; revisions create a traceable update; outcomes are
tracked prospectively. Begin as an in-app shadow/review feed, before new email delivery or
any signal-engine influence. See the
[original product review](2026-10-12-tradingview-copilot-tenji-review.md).

## 5. Add account-backed workspace preferences when needed

Browser-local arrangements and portable export/import already exist. Automatic cross-device
sync would require an authenticated preferences endpoint, account-scoped storage, a versioned
schema and conflict handling. Reuse the current layout validator rather than creating a second
layout format.

Acceptance: account isolation, offline editing, migration from existing browser preferences,
concurrent-device conflicts and reset behavior are defined and tested. Sync only arrangement
preferences, not plan inputs, model answers or credentials.

This is optional convenience work after chart controls and plan monitoring. An atomic
watchlist-move endpoint is another separate improvement; retain the current deferral until
adding/removing membership can commit together.

## Original recommended immediate scope (before authorization of all five)

Implement only item 1's state boundary and preservation tests first, then review that change
before adding model-driven commands. Prepare item 2's trigger contract and item 3's evaluation
cases alongside it. Keep the connections pilot, workspace sync and watchlist moves in separate
changes so their evidence and data requirements can be assessed independently.
