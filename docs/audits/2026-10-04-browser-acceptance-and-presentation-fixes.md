# Browser acceptance, presentation fixes, and what remains unverified

Date: October 4, 2026. Branch `prod`, commits `476211f6` and the frontend follow-up.

## Method, and its limit

Rendering was checked by running the project's **own compiled layout module** (`tsc`-emitted,
not a regex approximation) over the **real stored payload of report #15** in a headless Chromium
DOM. That makes the grouping, humanisation and section separation evidence about production
data rather than about a fixture written to match the code.

It is not a check of the live authenticated page. Three requested items could not be done: see
"Blocked" below.

## Verified in a real browser

**FIRST_FLASH is humanised.** The DOM contains "Initial figures — no cross-source reconciliation
performed" and the raw token `FIRST_FLASH` appears nowhere in `document.body.textContent`.

**Event information and current market context are separate sections.** Measured placement:

| Field | Section | Timeframe |
|---|---|---|
| `event_identity`, `fiscal_period` | event | identity |
| `revenue_actual`, `official_figures`, `guidance_change` | metrics | at_event |
| `price_as_of` | metrics | current |
| `signal_engine_assessment` | interpretation | current |
| `accounting_basis` | limitations | at_event |

Today's BUY signal and the quarter's reported revenue are no longer adjacent in one
undifferentiated list, which was the original defect.

**The timeframe heading is snapshot-relative**: "Market context at this snapshot's cutoff — not
at the event".

## Two defects the run found, both now fixed

**The section heading printed once per timeframe group.** A section with four timeframes rendered
"ANALYSIS LIMITATIONS" four times down the page, reading as four separate sections of the same
name — visible in the screenshots. It now prints once per section: the same payload produces
nine groups and **five** headings.

**The label column could not fit a phone.** It was a fixed `230px`; at a 390px viewport that
column plus the value column cannot fit, so the table forced the page sideways. Now proportional
(`34%`, floor `110px`), long tokens wrap, and the table scrolls inside its own container instead
of taking the page with it.

## The navigation bar is a capture artifact, not an overlay — demonstrated

The nav is `position: sticky; top: 0; z-index: 500` (`frontend/src/pages/_app.tsx:727`). Those
are two different questions and the screenshots could not separate them, so both were measured
against that exact rule over tall content:

- **Ordinary scrolling:** the bar's `top` in the viewport is `0` before scrolling and `0` after
  scrolling 1500px — pinned to the top, not across content. `elementFromPoint(570, 60)` returns
  a content row, so content below the bar is reachable.
- **Full-page capture:** the same bar lands **mid-image**, between rows 19 and 20, partially
  covering row 19 — reproducing exactly what the supplied screenshots show.

So the bar crossing mid-page content is a property of stitched full-page capture of a sticky
element. No layout change is warranted, and one made on this evidence would have been a fix for
a bug that does not exist in the browser.

## Correction links, verified against the real rows

Checked by calling `_forward_links` against production:

| Report | Subject | Points to | Kind | Payload |
|---|---|---|---|---|
| #5 | `earnings:MU:2026-06-24` | #12 | coverage | 22 fields intact |
| #7 | `earnings:MU:2026-06-24` | #12 | coverage | 25 fields intact |
| #11 | `earnings:MU:2026-08-31` | #12 | wrong_event | 25 fields intact |
| #12 | `earnings:MU:2026-09-30` | superseded_by #15 | — | 27 fields intact |

**The two kinds say different things, deliberately.** #11 analysed an event identified by a
substituted period end that is not an announcement date. #5/#7 did not: Micron's June quarter
really did report those figures, and nothing about them was replaced or found wrong. What no
longer holds is their standing as the *latest available* results, because the 30 September
release had not yet been ingested when they were generated. Wording them as "corrected" would
have quietly retracted accurate history, so the coverage notice is a distinct kind with its own
headline and a calmer colour.

## Blocked — needs a decision

Three requested checks need an authenticated session on the live site:

1. the event selector's interactive behaviour,
2. the correction banner as rendered on report #11 and #5,
3. real mobile interaction on the deployed page.

Minting a short-lived token on EC2 (secret never leaving the host) was refused by the
environment's credential policy, and I did not work around it. These stay **unverified**; the
items above are verified by the methods named, which do not substitute for them.

## Two claims corrected rather than defended

- The UTC-to-exchange conversion fixes publications that **cross** the local/UTC date boundary —
  for the US roughly 19:00 ET onward — **not** every after-close release. A 16:05 ET release is
  20:05 UTC the same day and was already dated correctly. Pinned by a test.
- `19 of 28 fields resolved` measures **completeness only**. Provenance (`sourced`), unresolved
  source conflicts (`conflicts`) and unverified comparability are now counted separately and are
  never summed into it, because one rising number reads as quality improving.
