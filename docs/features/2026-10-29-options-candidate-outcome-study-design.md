# Options candidate outcome study — design (October 29, 2026)

**Status (October 31): Part A run completed; results in [the results report](../audits/2026-10-31-options-candidate-study-results.md). Part B built and tested, not deployed.** Research only. No live setting, ranking or recommendation changes. See [Part A build](#part-a-build-october-30) and [Part B build](#part-b-build-october-30).

## The question

The decision desk shows four strategies (long call, protective put, covered call, cash-secured put), each with a nearest-strike preset and up to three alternatives. It also shows a "research lead" ranked by quote quality: narrowest spread, then open interest. Nothing has measured whether any of these choices did better than the others, or than doing nothing. Every "best pick", preference-tuning or calibration item (R14) waits on that measurement.

## Correction to the earlier framing

The October 29 next-steps list said this history "can't be rebuilt", which is only partly true. The chain archive already holds each contract's end-of-day NBBO, open interest, volume, IV and Greeks for 150 symbols since 2024-08-09, about 52.5 million rows. Candidates can therefore be regenerated for past sessions from archived chains, and outcomes marked from later archived sessions. That is how the options-income backtest (T399, 417 trades) already works.

What cannot be rebuilt is narrower: the exact set the live desk showed, with the live inputs and code version at the time (stop and target, holding period, preferences, snapshot timing). The design has two parts, and the replay comes first because it produces evidence now.

## Part A — archive replay (the main deliverable)

Follow the T399 rules (`src/backtest/options_income_backtest.py`):

1. **Replay the real builder.** For each entry session D, call the same `build_strategy_matrix` and candidate-review code the desk uses, with chains resolved strictly on or before D and D's own close as the stock price. No parallel "backtest version".
2. **No lookahead.** Inputs come only from data dated D or earlier. Data after D is used only to mark the outcome.
3. **Inputs that did not exist historically are not invented.** Stop and target levels from the Position Sizer/AI signal are not reliably stored per day, so the replay uses the builder's own fallback anchors (at-the-money for all four strategies), and the report says so. Holding windows use the existing SHORT/SWING/LONG/GROWTH defaults (the evaluation defaults already shown on the desk), each reported separately.
4. **Conservative fills.** Buy at the archived **ask**, sell at the archived **bid**, on entry and on exit. This is stricter than T399, which sold at the bid but had no buy legs. A candidate without a two-sided quote is not entered, and the skip is counted.
5. **Two exits, both reported.**
   - **(a) At expiry:** intrinsic value against the settlement close, using the same exact-session settlement rule as the income engine.
   - **(b) At the end of the holding window:** close the position at the archived bid (long) or ask (short) on that session.
6. **Every exclusion counted.** Unsupported contracts (R05/R09), missing quotes, expired or unparseable expiries, earnings inside the holding window (flagged, and also reported excluded), and symbols with no chain on D.

**Comparisons**, each per strategy × holding window:

- the research lead;
- the nearest-strike preset;
- a random qualifying candidate from the same shortlist (a fixed seed, recorded);
- no trade, plus the stock alone over the same window as context.

**Reporting rules**

- **Per month first, then pooled.** A pooled figure has hidden a flat recent period before, so the monthly breakdown is shown before any total.
- **Uncertainty clustered by trading day**, because candidates from one session are not independent.
- **Return on capital, not on premium** (premium for longs, collateral or shares for shorts), matching the calculator's existing convention.
- **Separate columns for win rate, mean net return, worst drawdown and capital-days.** A higher win rate with a lower mean is reported as worse.
- **A fixed held-out final period**, chosen before any results are looked at and evaluated once.

**What it will not model**, stated in the report as T399 does:

- real fills, size or price impact;
- early assignment, including calls before an ex-dividend date;
- intraday paths;
- anything outside the 150 captured symbols (survivorship);
- whether the archive's end-of-day NBBO was executable.

## Part B — a small daily record of what the desk actually showed

Part A cannot answer "did the leads the desk displayed do well with the inputs it really had". For that, capture once per session after the settled chain arrives, for the symbols the desk serves:

- the code fingerprint and method versions (`single_leg_candidates_preset_v1`, `covered_call_candidates_v1`, etc.);
- a hash of every input (chain session, stock reference, stop/target, horizon, preferences);
- the shortlisted contract IDs per strategy, the preset, the research lead and every exclusion reason.

The record is append-only and never rewritten; a change in rules files a new version beside the old one. Outcomes come later from the same Part A marking code. Storage is a few kilobytes per symbol per day, with no extra provider requests.

## Order and gates

1. **Part A, first runs.** Report only; no change to any default. Expected size: tens of thousands of candidate-entries per strategy × window across 150 symbols.
2. **Review of the Part A report** before anything is promoted. Unexpected results (for example, the research lead doing worse than random) get root-caused before any conclusion is drawn.
3. **Part B,** once Part A's marking code exists, so both use one outcome definition.
4. **Any change to ranking or defaults** is a separate, versioned proposal, with its own held-out and forward evidence, per R14.

## Decisions needed before building

- Which holding windows to include (default: all four existing horizons).
- Whether earnings-window candidates are excluded or reported separately (default: both).
- Start date of the held-out period (proposed: the most recent full quarter, frozen before the first run).

## Part A build (October 30)

The decisions above were taken with the proposed defaults: all four holding windows; earnings-window candidates reported both included and excluded; held-out period **2026-07-01 to 2026-09-30**, frozen in code (`DEVELOPMENT_END`, `HELD_OUT`) before any run. Entries after the held-out period are labelled `post_freeze` and reported separately.

**Code.** `services/market-data/src/backtest/options_candidate_study.py`:
- It replays the real `build_strategy_matrix`, the route's `_nearest_expiry_in_dte_window` and the real archive-row adapter (`_to_chain_row` plus contract terms).
- The only port is the research lead, which lives in the frontend; it is in `src/services/option_research_lead.py`, shared with Part B. Thirty shared golden cases in `tests/fixtures/option_research_lead_golden.json` pin it: the TypeScript reference generates the expectations (`frontend/src/lib/optionResearchLead.golden.test.ts`), and the Python port must reproduce them exactly.
- Entries are on the first archived session of each ISO week. The stored close is used only where that session's put-call parity agrees with it.
- Buys fill at the ask and sells at the bid. Holding exits close at the first session on or after entry plus H days; expiry exits use intrinsic value at the expiry session's basis-checked close.
- Every skipped entry, missing quote, basis failure and still-open contract is counted.
- Each symbol's archive is streamed once, in chronological 92-day chunks, inside read-only transactions with a 90 s statement timeout and a 0.5 s pause between chunks.

**Tests.**
- `services/market-data/tests/test_options_candidate_study.py`, 48 tests: golden lead parity; constants matching the route and frontend; fill and exit rules; P&L and capital for all four strategies; frozen periods; and a synthetic parity-consistent archive covering weekly entries, real-builder use, fills read from the chosen contract, no lookahead, split-adjusted close blocked, missing exit quote counted, still-open contracts counted, earnings flagged, matched-only pairing, the earnings split, the monthly table, and resampling by date.
- Sabotage of the ask fill, the basis gate, the OI tie-break or the weekly rule each fails its test.
- Full suites: backend **6,130 passed / 31 skipped**, frontend **1,033 passed**, typecheck clean.
- The deployed `options_strategies.py`, `uw_option_chain.py` and `options_contract.py` hash-match the tested code, and the deployed expiry picker and its constants are byte-identical (October 31, 00:17 ET).

**Production run: performed October 31, 2026** with the user's permission (285,702 trades; [results](../audits/2026-10-31-options-candidate-study-results.md)). The run reads the whole archive once (about 52.5 million rows), and only production holds that data. Launching it inside the market-data container (copying the harness to `/tmp`, not `/app/src`, and starting a detached read-only process) was refused by the session's permission policy as a remote shell write. To run it, with permission and outside US market hours:

```bash
# from the repository root
scp -i ~/Documents/Stock_AI/lausing.pem services/market-data/src/backtest/options_candidate_study.py \
    services/market-data/src/backtest/options_candidate_study_launch.py \
    services/market-data/src/services/option_research_lead.py ec2-user@18.205.121.71:/tmp/
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 '
  docker exec stockai-market-data-1 mkdir -p /tmp/ocs &&
  docker cp /tmp/options_candidate_study.py stockai-market-data-1:/tmp/ocs/ &&
  docker cp /tmp/option_research_lead.py stockai-market-data-1:/tmp/ocs/ &&
  docker cp /tmp/options_candidate_study_launch.py stockai-market-data-1:/tmp/ocs/launch.py &&
  docker exec -d -w /app stockai-market-data-1 sh -c "python /tmp/ocs/launch.py /tmp/ocs_run > /tmp/ocs_run.log 2>&1"'
# progress: one JSON line per symbol in /tmp/ocs_run.log
# results:  /tmp/ocs_run/summary.json, provenance.json, trades.jsonl (inside the container)
```

Alternatively, once committed and deployed normally, `python -m src.backtest.options_candidate_study /tmp/ocs_run` runs it from `/app`. Write the results report from `summary.json` in this order: the monthly table first, then the paired comparisons (lead minus preset, lead minus random) with their date-clustered intervals, then the counters.

## Part B build (October 30)

**What is recorded.** `services/market-data/src/services/options_desk_record.py` records the game-plan response the desk was actually served (`GET /{symbol}/options-game-plan`). It is called from the route after the response is built, so it makes no extra provider request and no extra archive read. Each row stores:
- the ET date, symbol and chain session;
- the inputs: stop, target, shares, holding period, search preferences, reference price, chain as-of and refused-contract counts;
- per strategy: method version, scope, anchor, expiry or searched expiries, failed expiries, evaluated, screened and rejected counts, the original/preset contract, each alternative (contract ID, strike, expiry, mid, spread, OI, IV, delta) and the research lead;
- a code fingerprint: SHA-256 of the candidate modules and of the route functions on this path, so an unrelated route edit does not count as a rule change.

No user identity is stored.

**Rules.**
- **Append-only.** Table `options_desk_records_v1`, keyed by (date, symbol, record hash). The hash covers the code fingerprint, inputs and shortlist. A repeat of the same request on the same day files nothing; a different input, result or rule files a new row beside the old one. Nothing updates or deletes a row.
- **The lead is never guessed.** It is recomputed with the golden-pinned port only when the desk would have computed it without search preferences and with an integer holding period. Otherwise the row says `not_recorded_with_preferences` or `not_recorded_without_holding_period`.
- **Recording cannot affect the response.** It uses the route's own session inside a savepoint (a second connection per request is the AUD-CONNPOOL-NESTEDSESSION pattern) and swallows and logs any failure.
- **Outcomes are not computed here.** They will come from Part A's marking code, so both parts share one outcome definition.
- A new table only, created by `create_all` at startup; no existing table is altered.

**What it is not.** It records what the API served, not what a user's screen displayed after client-side filtering. The research lead is the server-side port of the frontend rule.

**Tests.** `tests/test_options_desk_record.py`, 10 tests:
- the record matches the matrix actually built, for all four strategies;
- the lead equals the port's answer for the requested holding period, or carries its explicit reason;
- input and record hashes behave as described;
- the fingerprint covers the route functions;
- recording never raises, and a plan without reviews files nothing;
- the real route passes its own session and the exact response it served, and a recording failure leaves the response unchanged.

A real-SQL probe (`tests/_options_desk_record_probe.py`) checks the append-only behaviour; it passed on SQLite (in the suite) and on a local PostgreSQL with temporary tables, rolled back.

**Not deployed.** Records start only after market-data is deployed with this change, so there is no history from before that.
