# Check-back calendar — remaining work and data-gated items

**Drafted 2026-09-30.** Requested as "create a calendar to check back for me … for the work we
left and also those need data to verify."

> **STATUS: CREATED 2026-10-01 02:54 UTC.** All 8 events exist in `stockai2028@gmail.com`
> (calendar `primary`). **Do not replay the JSON beside this file — it would duplicate them.**
>
> The first attempt failed with `invalid_grant`: the stored Google OAuth grant was dead even
> though the MCP server itself was connected and healthy. Re-auth needed a specific sequence,
> because the server refuses to re-add over an existing nickname *and* refuses to remove the
> last account — add a NEW nickname to force a fresh consent flow.

Dates are America/Los_Angeles. Every item traces to the M01–M25 register in
`docs/features/2026-09-30-measurement-framework-and-improvement-backlog.md` and the status map in
`docs/audits/2026-10-01-session-status-completed-and-remaining.md`.

## Why these dates and not others

The schedule is built on **what each item is actually waiting for**, which is not the same thing
for any two of them:

| Gate type | Items | What sets the date |
|---|---|---|
| A decision only you can make | M02, M21 | M02 **expires ~Oct 6** — after that the decision is moot |
| One specific scheduled run | M19 (MU-01) | First instrumented sync, morning of Oct 1 |
| An event that has to happen | M16, M19 (MU-02 live) | Earnings season; cannot be forced |
| Accumulated outcomes | M06–M08, M10, M11, M15 | Monthly re-count; defer again if the count hasn't moved |
| Someone doing the work | M13, M14, M20, M24, M25 | Not data-gated at all |
| The market doing something | M12 | One bear sample. Quarterly, and may stay unanswerable |

The monthly and quarterly checks are deliberately **re-count-then-defer**, not deadlines. That is
the discipline the `project_pending_data_verification` memory exists to enforce: if the count has
not moved, the correct output is another deferral, not a conclusion drawn from thin data.

## The events

| When | Item | Gate |
|---|---|---|
| Oct 1, 08:00 | MU-01 — read the first instrumented earnings sync | Scheduled run |
| Oct 3, 09:00 | **DECISION**: recovery markers, portfolios 2 and 5 | Expires ~Oct 6 |
| Oct 3, 09:30 | **DECISION**: signal-outage recovery digest | Your approval |
| Oct 8, 09:00 | Engineering backlog — M13, M14, M20, M24, M25 | None |
| Oct 15, 09:00 | MU-02 live verification + broker fill re-poll | Occurrence |
| Nov 2, 09:00 | Monthly data check — the six collecting items | Outcome count |
| Dec 1, 09:00 | GEX control arm — review trigger | ~30 control events |
| Dec 31, 09:00 | Regime robustness + dark-pool baseline replay | Market / work |

Full briefing text for each lives in the JSON's `description` fields, so the detail travels into
the calendar entry itself rather than requiring this file to be open.

## Three cautions carried into the event text deliberately

These are in the descriptions because they are the specific ways each check could be read wrong:

1. **MU-01** — `eps_actual=None` in a `history_map` line is *not* proof of a mapping defect. If
   the provider returned no actual, that line is correct output. The decisive evidence is the
   four-step chain, and if the logs cannot connect it, the honest result is an **evidence gap**,
   not an inferred cause.
2. **M09 (GEX)** — ~30 control events is a **review trigger, not a promotion threshold**. Even
   worse outcomes among corroborated alerts would not establish that *using* the gate hurts
   portfolio returns; that needs a baseline-vs-gated comparison including avoided trades.
3. **M05 (dark pool)** — the relative bar rejects 15 of 9,707 prints because it sits below the
   absolute floor for 55 of 57 symbols. A bar that never binds has not been shown not to work.
   It was never given a value at which it could act.

## Already on the calendar — not duplicated here

Per `project_deferred_checks_calendar`, two checkpoints already exist: **2026-10-06** (did the
fixes take effect) and **2026-12-04** (did they work). Those carry the `|t_day| < 2` rule —
inside two days of a change, a result is **NOT YET MEASURABLE**, which is not the same as failure.
The events above are additions, not replacements.
