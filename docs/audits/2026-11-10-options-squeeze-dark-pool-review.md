# Options, short squeeze and dark pool: implementation review (November 10, 2026)

**Fixes applied the same day and deployed November 11 (`f0d941ab`, market-data as a file-level deploy).** See [Fixes applied](#fixes-applied-november-10-not-deployed). Production data and settings were not touched. Companion design: [Options tab redesign and Call Finder](../features/2026-11-10-options-tab-call-finder-design.md).

## Scope and method

- **Code reviewed:** `prod` at `38adf591`, by reading. Nothing was run against production, and no alert, email or page was exercised live.
- **Options:** reviewed directly: the stock page's Options tab, the chain and game-plan endpoints, the strategy builder, and the first 200 lines of the options-flow alert job. The remainder of that job, the gamma-unwind alert and the options-income engine were **not** re-read today.
- **Short squeeze and dark pool:** each reviewed by a read-only agent with a narrow brief. For dark pool, I checked findings D1–D5 against the code myself; the rest are recorded as the agent reported them and marked so. The short-squeeze section says the same for its own findings.
- **Not repeated here:** measurements already on record, such as the Short Squeeze Alert's 13.3% win rate (n = 15, September 24) and the dark-pool relative threshold rarely binding (September 30).
- "Confirmed" below means confirmed by reading the code path. None of these was reproduced at runtime.

## Summary

| Area | State | Most important finding |
|---|---|---|
| Options tab | Works as built, but hard to use for choosing a contract | Delta and gamma are returned by the API and never shown; the chain is at the bottom, collapsed, and hidden when the flow summary fails |
| Options strategy builder | Sound; hardened by earlier audits | The IV-rank input to its recommendation is never supplied on the stock page |
| Options flow alert | The part read is sound | Not fully re-read today |
| Dark pool alert | One real duplicate-email defect; several wording and display defects | The same print can be emailed twice (D1) |
| Short squeeze | Real logic defects in scoring and alert timing | Days-to-cover is scored backwards in the alert and screener, against the app's own guide and stock page (S1) |
| Earlier study work | Not on `prod` | The candidate study, its results and the desk record exist only on a safety branch |

## Options

### O1. Earlier options research work is not on `prod` (confirmed)

The candidate outcome study, its October 31 results report, the daily desk record and the dark-pool repeated-ID classification are in commit `899ae78b` on branch `safety/prod-before-reconcile-20261108`. They are not ancestors of `prod`, and none of the files exist in the working tree. The desk record was therefore never deployed, and no forward record of served candidates exists.

**Decision needed:** bring that work onto `prod`, or leave it parked. The Call Finder's validation depends on it.

### O2. The chain shows no greeks, although the API returns them (confirmed)

`uw_option_chain._to_chain_row` returns `delta`, `gamma`, `theta`, `vega` and `rho` for every row. `OptionsChainTable.tsx` renders only bid, ask, last, volume, OI and IV. A reader cannot compare contracts on delta or gamma anywhere on the tab except inside the strategy cards.

The provider sends greeks only for contracts that traded that day, so many rows will have none. The design covers this with estimated values, labelled.

### O3. The chain is hidden when the flow summary is unavailable (confirmed)

In `pages/stock/[symbol].tsx` the whole "Options Chain" block renders only when `optionsFlow && optionsFlow.available`. The chain comes from a different endpoint. A flow-summary failure or an empty summary hides a chain that would have loaded.

### O4. The strategy recommendation never receives IV rank on the stock page (confirmed; intent unclear)

`build_strategy_matrix` chooses its recommendation partly by IV regime (rich IV favours selling premium). `get_options_game_plan` calls `compute_options_game_plan` without `iv_rank` and with `signal=None`, so the regime is always `unknown` there. The frontend component does not display the regime either.

This may be deliberate: the October audits moved the desk to research-only wording. If so, the dormant branch should be documented as retired. If not, the T402 behaviour ("recommendation keyed to position, then IV regime") is not in effect on this page.

### O5. Usability gaps on the tab (confirmed)

- **Order:** nine blocks in one column. The chain, the thing a contract search needs, is last and collapsed.
- **No cross-expiry view:** the chain shows one expiry at a time, and nothing compares a strike across expiries.
- **No summary line:** price, IV, IV rank, implied move and event dates are spread across separate cards or absent.
- **Missing quotes read as prices:** a contract with no bid or ask shows `0.00`, because prices keep a 0.0 convention where counts and IV already show "—".
- **Expiry buttons** carry no days-to-expiry and are not grouped, so LEAPS are found only by reading dates.
- **Access:** the Game Plan and its strategy cards are advanced-tier only; the chain is visible to everyone.

### O6. Options flow alert: the part read is sound

Lines read: lock, market-hours gate (US only), audience and opt-out filter, side classification with ties and missing sides excluded, event-time quarantine, freshness window, newest-event-wins with a deterministic tie-break. No defect found in that part. The rest of the job (delivery, cooldown, outcome recording) was not re-read today, and this is not a clearance of it.

Its audience has the same shape as D2 below: every user with any open price alert receives every candidate from a shared symbol list. I did not check that email's wording.

## Dark pool

Findings D1–D5 were checked against the code by me. D6–D12 are the review agent's findings, not independently checked.

### D1. The same print can be emailed twice (confirmed)

- A print is a candidate while it is under 90 minutes old (`_DARK_POOL_PRINT_MAX_AGE_MINUTES = 90`).
- The only repeat guard is a per-print Redis key that expires after 60 minutes (`_DARK_POOL_ALERT_COOLDOWN_MINUTES = 60`). There is no seen-set.
- **Example:** a $5M print executes at 10:00 and is emailed at 10:05. The key expires at 11:05. At 11:06 the print is 66 minutes old, still inside the window and still the largest, so the key is claimed again and the same email goes out.
- This applies whenever the first email goes out less than 30 minutes after execution, which is the normal case.
- The comments beside both constants say a sent print "can never re-send". That is not true as written.

**Smallest fix:** make the key's lifetime longer than the age window (for example 120 minutes), with a test that replays one print across 91 minutes.

### D2. The email says "on your watched symbols" for symbols the reader does not watch (confirmed)

Recipients are every user with any open price alert who has not opted out. Candidates come from all users' alert symbols plus the top symbols by K-Score. Every recipient receives every candidate. `email_service.py:2280` describes them as "on your watched symbols". A user watching only AAPL can receive a TSM print under that sentence.

Either the wording or the audience should change. The October 24 "owned-list audience" work did this for other alerts.

### D3. A feed failure can be reported as "the provider returned none" (confirmed)

`/{symbol}/dark-pool-prints` sets `status: "empty"` whenever the filtered list is empty. That overwrites the real status. Two cases produce it wrongly:
- the fetch failed and the retained sample holds only canceled prints;
- the fetch succeeded but every print was canceled or held out as conflicting.

The panel then says the provider returned none.

### D4. Prints without a provider ID are listed first, and the panel shows only eight (confirmed in code; frequency unknown)

`usable_dark_pool_rows` returns ID-less rows first, then identified ones, and the route does not re-sort. The panel shows the first eight. With eight older ID-less prints and three newer identified ones, the newest are hidden.

### D5. The alert can fire on prints the baseline can never contain (confirmed in code; frequency unknown)

The alert accepts rows with no tracking ID, and identified rows whose canceled flag is missing. The ledger rejects both, so they never enter any baseline, cannot be de-duplicated by identity and cannot be matched to a later cancellation.

### D6–D12. Reported by the agent, not independently checked

| # | Finding | Agent's confidence |
|---|---|---|
| D6 | Polls arrive every 900–960 seconds but coverage counts fixed 900-second slots, so at least one slot reads "missing" each session and coverage reads `degraded` every day | Plausible; needs a production check |
| D7 | The coverage summary spans 21 days while the baseline spans 14, so an outage 18 days ago shows beside a baseline it did not affect | Confirmed, low severity |
| D8 | When a print has a premium but no price, the stored alert price is 0.0 or the live price, and the tab shows it as the execution price | Confirmed in code; rare |
| D9 | Email wording: "+N more prints today" counts extra symbols in one cycle, not prints or the day; "dark pool venue" claims more than a reporting-facility code establishes | Confirmed |
| D10 | The Dark Pool tab does not show baseline status or sampling coverage, so a print that passed only the $1M floor because the baseline query failed looks like any other | Confirmed |
| D11 | No regular-session gate; the "live" price in the email is not checked for age, so an evening print may be compared with the 16:00 close | Plausible |
| D12 | The repeat key uses the execution time, so two different prints in one symbol in the same second share a key and the second is suppressed | Confirmed, minor |

**Checked and found correct (agent):** side classification with missing, crossed or zero-width quotes; baseline excludes canceled, ambiguous and null-premium rows and is strictly before the event; time parsing rejects naive and future times; older observations cannot overwrite newer ones; opt-out and unsubscribe footer applied; cooldown key released on a failed send; recent-alerts route ordering and null handling; panel null rendering.

**Still unmeasured:** polling completeness in production. The read was refused by the permission policy on October 29 and October 31. D6 means the current coverage figure would overstate gaps even when measured.

## Short squeeze

I checked S1, S2, S3 and S5 against the code. S4 and S6–S16 are the review agent's findings, not independently checked. Paths are under `services/market-data/src/` unless stated.

### S1. Days-to-cover is scored backwards in the alert and the screener (confirmed)

Days to cover is shares short ÷ average daily volume. A **high** number means shorts need many days of normal volume to exit, which is the trapped position a squeeze needs. A **low** number means they can exit easily. The app disagrees with itself:

| Place | Treats as most acute |
|---|---|
| Alert "CRITICAL" tier: `scheduler.py` `_SQUEEZE_CRITICAL_DAYS_TO_COVER = 2.0`, flagged when `short_ratio <= 2.0`; the email calls it "a critically thin exit" | **Low** |
| Screener score: `routes.py` `dtc_pts = (4.65 − dtc) / (4.65 − 1.13) × 30`; the docstring says "LOWER is more acute" | **Low** |
| Paper-trading squeeze score (uses the screener score, per the agent) | **Low** |
| Screener's own guide, `frontend/src/pages/short-squeeze.tsx`: "Under ~1-2 days, shorts can slip out quietly; 5+ days means real trapped exposure" | **High** |
| Stock page squeeze score: `ratioPts = ratio >= 8 ? 30 : … : 0` | **High** |

**Example:** two stocks, each 25% short. A has 1.0 days to cover; B has 9.
- The alert marks A "CRITICAL" and does not flag B.
- The screener gives A 30 points and B 0.
- The stock page gives A 0 and B 30.

The August 13 calibration chose the threshold from real percentiles, but on the wrong side of them. The guide and the stock page match standard squeeze analysis.

**Fix (needs a decision):** invert the alert tier and the screener term, then re-check the alert's measured accuracy, since it was gathered under the inverted rule.

### S2. Bearish-puts watches revert for a timing reason, not because pressure faded (confirmed)

The bearish cache keeps only puts-dominant rows whose nearest expiry is **3 to 5 days** out. The watch-revert checker treats a symbol missing from a fresh cache as faded.
- **Weekly options:** a Monday watch on a stock with Friday expiries is 4 days out. By Wednesday it is 2 days out, drops out of the cache, and the watch reverts with an email saying the pressure "has faded" as expiry approaches.
- **Daily expiries:** symbols such as SPY are always 0–1 days out, so a watch on one reverts on the first fresh scan.
- **Dead check:** the "no longer puts-dominant" branch can never be true, because the cache holds only puts-dominant rows.

### S3. The pre-breakout email's "ML price-direction" figure has no direction (confirmed)

- The ML service computes `confidence = abs(prob − 0.5) × 200`, so a strongly bearish model reading (10% bullish) gives 80.
- The scheduler reads only `confidence`, and the email prints "General ML price-direction read: 80 confidence" under a breakout heading.
- The same direction-less value is stored on the outcome row.
- According to the agent, a missing model returns 0, which prints "0 confidence".

### S4. The same symbol can trigger repeated Short Squeeze and Squeeze Watch emails in one day (agent)

De-duplication compares only against the previous minute's set, and there is no daily cap per symbol.
- **Example:** a move of 3.1%, then 2.9%, then 3.2% gives a Short Squeeze email, then a Watch email, then a second Short Squeeze email.
- **Other triggers:** a rising volume threshold through the session, one missed live-price cycle, or a fundamentals-cache lapse can each drop a symbol for one cycle.
- **Hidden from the outcome table:** it records one row per day, so the repeats don't show there.

### S5. Pre-breakout setups re-email every trading day (confirmed)

- **Short repeat guard:** the de-duplication set expires after 20 hours. The job runs every 4 hours during US market hours only, so a run on one day is not covered by the previous day's set. Monday always re-sends after the weekend.
- **Daily repeats:** compression lasts days to weeks, so a coiling stock emails daily.
- **Inflated sample:** each repeat adds a near-duplicate outcome row, pushing the sample toward the 30-outcome calibration floor.

### S6–S16. Reported by the agent, not independently checked

| # | Finding | Agent's confidence |
|---|---|---|
| S6 | "Entry price" on the performance page is described as the live alert price, but is the next session's close. The "entry gap" is roughly a one-day return, not an execution gap. The admin backtest uses the same-day close, so its win rate is not comparable with the live tracker's | Confirmed |
| S7 | The squeeze screen shows no data age: short-interest date, staleness and ranking date are returned but never rendered | Confirmed |
| S8 | Alert scanners never check that a live-price row is from today; at the open, yesterday's move and volume can trigger an alert | Plausible |
| S9 | The options-expiry alert uses the UTC date in the evening, so days to expiry and the outcome date shift by one | Confirmed |
| S10 | Pre-breakout compression uses today's unfinished daily bar, biasing it toward "compressed" early in the day; no recency check on the newest bar | Mechanism confirmed; size plausible |
| S11 | The Squeeze Watch email labels its win rate as the short_squeeze family's; it comes from squeeze_ignition | Confirmed |
| S12 | Deactivated accounts are not filtered out of the four squeeze emails | Confirmed |
| S13 | The options-expiry alert fills missing open interest with 0, so a missing put side can read as 100% calls | Plausible |
| S14 | The Unusual Whales short-interest row is taken as the first list element; if the list is oldest-first, the "UW disagrees" check compares against the oldest reading. This is the same pattern as the IV-rank bug fixed in September | Plausible |
| S15 | The paper-trading squeeze score and the admin backtest have no short-interest age check | Confirmed |
| S16 | Small display issues: "Infinity% MoM" when the prior month is 0; short interest above 100% of float falls outside every calibration band; NaN short interest passes `< 15` gates; US half-days run until 16:00 | Confirmed |

**Checked and found correct (agent):**
- the 30-day short-interest age reject in all three short-interest alerts;
- percent and fraction units handled consistently;
- division guards;
- per-market hours gates, including the HK lunch break;
- opt-out and unsubscribe;
- failed sends do not write de-duplication state;
- outcome evaluation uses the ET date and next-session entry;
- the screener selects the latest ranking;
- short-interest dates shown in the emails;
- null rendering on the screener.

## Suggested order of work (nothing started)

1. **S1** days-to-cover direction: it changes what the alert and screener rank. Needs your confirmation of the direction first.
2. **D1, S4, S5** repeat emails: small, user-visible, testable.
3. **S2** watch reverts, **S3** ML figure, **D2/D9** wording, **D3** status: small and independent.
4. **O1** decision on the safety-branch work.
5. **Options tab and Call Finder**, per the design, after its four decisions.
6. The rest, then production checks of D6, D11, S8, S13 and S14.

## Fixes applied (November 10, not deployed)

Done at your request ("let's fix them"). All changes are local and uncommitted.

### Status of each finding

| Finding | Status | Change |
|---|---|---|
| S1 days-to-cover backwards | **Fixed** | Alert critical tier is now `>= 8.0` days (was `<= 2.0`). Screener term scores 0 at ≤ 3 days and 30 at ≥ 8, linear between: the stock page's own bands. Email copy says "or more days" and "a high reading". Paper trading inherits the screener score |
| S2 bearish watch reverts on expiry distance | **Fixed** | The gamma scan publishes every symbol it measured, with its put share (`stockai:bearish_puts_scan`). A watch fades only when a fresh scan measured a put share below 55%; not measured is not a fade |
| S3 direction-less ML figure | **Fixed** | The email shows the model's probability of a rise and its direction. The no-model placeholder is treated as unavailable. The stored column keeps its original meaning, so old rows stay comparable |
| S4 same-day repeat squeeze emails | **Fixed** | Per-recipient, per-ET-day record of symbols already emailed, for Short Squeeze and Squeeze Watch separately |
| S5 pre-breakout daily re-email | **Fixed** | Active set lasts 5 days (was 20 h). The outcome table records one row per setup episode (none within 7 days), not one per day |
| S6 entry price mislabelled | **Fixed** | Wording on the performance page and in the email. The admin backtest now uses the live tracker's rule: entry at the next session's close, windows counted from the entry. The response states the rule |
| S7 no data age on the squeeze screen | **Fixed** | Short-interest date and a stale marker shown on each row; stale ranking date shown beside momentum. The 🔥 badge now requires a current ranking and current short interest |
| S8 yesterday's live row | **Fixed** | A row whose `session_date` is before today in its own market is skipped in both squeeze jobs. An undated row is kept |
| S9 UTC date in the gamma alert | **Fixed** | ET date for days-to-expiry and for the shared squeeze outcome recorder |
| S10 forming daily bar | **Fixed** | Settled bars only (before today, ET); newest settled bar must be within 7 days |
| S11 win-rate label | **Fixed** | "Squeeze Watch / ignition alerts" |
| S12 inactive accounts | **Fixed** | `filter_recipients` drops inactive accounts. This applies to **every batch alert**, not only squeeze |
| S13 missing OI as zero | **Fixed** | A side with no reported near-money OI means the concentration is not measured, so the symbol is skipped |
| S14 short-interest row order | **Fixed** | Newest row by `market_date`, correct under either ordering |
| S15 no short-interest age check | **Paper path fixed**; admin backtest **cannot** apply it | The paper squeeze score applies the alerts' 30-day rule. Weekly snapshots store no settlement date, so the backtest response now states that limit and the overlapping-days caveat |
| S16 display edge cases | **Fixed** | MoM shown only with a positive prior month; `_safe` treats NaN as missing; the 30+ short-interest band is open-ended, so >100% gets its rate; the US session closes at 13:00 ET on NYSE half-days (shared with paper trading) |
| D1 same print emailed twice | **Fixed** | A per-print key lasts 120 min, longer than the 90-min window |
| D2 "your watched symbols" | **Fixed (audience)** | Each recipient receives only symbols they follow (watchlists, resolved both ways, plus open price alerts). A lookup failure sends nothing. Up to 40 followed symbols are added to the scan (most-followed first, about 1,000 extra provider requests a day at most). The recipient set is unchanged, so no account is enrolled by omission |
| D3 failure shown as "returned none" | **Fixed** | The fetch status is kept. A response with prints but none usable says so, with the count |
| D4 unidentified prints first | **Fixed** | Prints listed newest first; future-stamped or undateable prints last |
| D5 alert on prints the baseline rejects | **Fixed** | The alert requires a provider ID and an explicit cancel flag, the ledger's rule |
| D6 coverage drift | **Fixed** | A slot counts as covered by a poll up to 120 s after its end. Reproduced first: steady 930–960 s polling showed 1–2 missing slots per session |
| D7 21-day coverage beside 14-day baseline | **Fixed** | Coverage is read over the baseline's 14 days |
| D8 no price stored as $0.00 | **Fixed** | A print with no execution price does not alert |
| D9 email wording | **Fixed** | "+N more symbols with a qualifying print in this check"; venue described as a reporting-facility code |
| D10 tab lacks baseline status | **Fixed** | A new market-data-only table `dark_pool_alert_baselines_v1` stores each alert's baseline (no `shared/` change, so no 12-container rebuild). The tab has a Baseline column: the multiple and median with sample size, or "$1M floor only" with the reason |
| D11 no session gate; live price age | **Fixed (decided)** | Polling and capture continue around the clock; emails and outcome rows only during the regular US session. A live price from an earlier session is no longer shown as live |
| D12 same-second key collision | **Fixed** | Key uses the provider ID, else execution time plus premium |
| O2 chain hides greeks | **Fixed** | Delta and Gamma columns per side; "—" when the provider sent none |
| O3 chain hidden on flow failure | **Fixed** | Chain section always offered; its own endpoint reports "no chain" |
| O5 missing ask shown as 0.00 | **Fixed** | An ask of 0 shows "—". A bid of 0 is kept, since it can be a real quote |
| O4 IV rank not reaching the desk | **Fixed** | The route passes the cached 1-year IV rank, used only when dated within 7 days. Its date and status are returned |
| O1 safety-branch work | **Decision pending** | Explained to the user; not merged |

### Verification

- **Backend:** `make test`. market-data 6,247 passed with 2 failures, `test_prebreakout_dataset` and `test_option_ledger_requests`. Both fail identically on an unmodified `HEAD` checkout, so they predate this work. The other 12 suites pass, including research-engine. The T401 ratchet is at its baseline: three numeric source-text assertions I first wrote were converted to value tests.
- **Frontend:** 1,018 passed (88 files); TypeScript clean.
- **Sabotage:** reintroducing S1 (both sites), S2, S3, S4, S5, S8, S13, D1 and D6 each fails its tests.

### Effects to expect after deployment

- **S1:** the squeeze screener's ranking and the alert's CRITICAL flag will change for most symbols. The alert's earlier measured accuracy was collected under the inverted rule.
- **S5:** pre-breakout outcome rows drop from one per qualifying day to one per episode. The win rate's sample will grow more slowly.
- **D5 and D8:** a few dark-pool alerts will no longer fire, for prints that have no identity or no price.
- **S12:** deactivated accounts stop receiving every batch alert type, not only squeeze emails.

## Deployment (November 11, 2026, about 05:10–05:40 UTC)

**Commit `f0d941ab`** (rebased onto 20 commits from other sessions; the combined tree was re-tested before pushing).

- **market-data: a file-level deploy, not an image rebuild.** `prod` also holds two changes another session keeps deliberately undeployed: `58d77de4` (part of a running RZY A/B read) and `3369e1a5` (price risk). An image rebuild from `prod` would have shipped them. Instead, under the host deploy lock:
  1. The container's copies of the 10 modified files were checked against release `16fe5a81` (all matched).
  2. Those copies and the container log were saved to `~/container-logs/prehotfix-f0d941ab-20261111T051059Z/`, with a list of the 7 new files.
  3. The 17 files were copied in and verified by hash.
  4. The container was restarted: healthy after about 40 s.

  None of the 17 files is touched by the held commits or by the other session's live hot-patch.
- **Frontend:** `scripts/deploy_frontend.sh`, built on EC2 from `f0d941ab`, healthy. The served chunks contain the new code (Call Finder, Baseline column, short-interest dates) and no `localhost` URLs.
- **Live checks (read-only, in the container):**
  - the three new tables exist;
  - the dark-pool recent route returns the scope and baseline fields;
  - `/api/stocks/{symbol}/options-call-finder` answers 401 through the gateway (routed, login required).
- **The Call Finder on real data** (session 2026-11-09):

| Symbol | Near-term pick | LEAPS pick | Note |
|---|---|---|---|
| AAPL | Dec 18 $335 call, delta 0.74 | Jan 2028 $310 call, delta 0.76 | |
| NVDA | Dec 18 $168 call, delta 0.67 | Jan 2028 $150 call, delta 0.76 | IV rank 96: the rich-IV warning shows |

- **Log lines after restart:** Yahoo Finance "currently down" and `US10Y` 404s also appear before the restart (89 and 2,504 lines in the saved log), so they are not from this deploy.
- **Caveat:**
  - A recreation of the market-data container reverts this deploy until the next image build.
  - The next image build from `prod` ships these changes permanently, and with them the two held commits. That build needs the other session's decision on `58d77de4` and `3369e1a5` first.
  - Until then the drift check will report these 17 files as differing from the image.

## Follow-up (November 11): options-flow audience and one IV-rank definition

- **Options-flow email, same rule as dark pool (AUD-OF-AUDIENCE).** Each recipient now gets only the symbols they follow (watchlists plus open price alerts). A failed lookup sends nothing. The recipient set is unchanged. Up to 20 followed symbols are added to the scan. That is fewer than dark pool's 40, because this job reads the provider every 150 s per symbol (~150 requests per symbol per session, ~3,000/day at the cap). The email header says the symbols are ones the reader follows.
- **IV rank "rich/cheap" aligned at 70/30 (AUD-OPT-IVRANK-ALIGN).**
  - "Rich" was 60 in the strategy builder's recommendation and the Call Finder's warning. It was 70 in the alert email's badge, the screener and the desk's own preference rule, so one stock could read "rich" in one panel and "mid-range" in the next.
  - One constant now applies on each side (`options_strategies.IV_RANK_RICH/CHEAP`, `frontend/src/lib/ivRank.ts`), and a backend test fails if the two differ.
  - Effect: IV ranks of 60–69 now read "normal", not "rich", in the builder's recommendation and the finder's warning. This is a convention, not a measured edge.
- **Tests:**
  - market-data: 6,366 passed, 1 failure that also fails on unmodified `HEAD` (`test_prebreakout_dataset`);
  - frontend: 1,059 passed, typecheck clean;
  - sabotage of the per-user filter and of either threshold is caught.
