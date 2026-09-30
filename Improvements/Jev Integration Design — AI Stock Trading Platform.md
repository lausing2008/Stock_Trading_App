# Jev Integration Design — AI Stock Trading Platform

Sep 29, 2026 · @Richard Lau

## Overview and goals

Jev becomes an enrichment layer that turns unstructured market text into typed, stored features; deterministic code keeps every buy, sell and sizing decision. Jev is TypeSafe's System One decision model, served on OpenRouter as `typesafe/jev-1.13`. It takes a `state` plus typed questions and returns probabilities, not text ([Jev on OpenRouter](https://openrouter.ai/docs/guides/community/jev)).

Goals:

- Filter news, filings and disclosures so only material items reach a trade setup.
- Add catalyst type and sentiment as features next to price-based signals.
- Veto technically valid setups when the text context is clearly hostile.
- Tag closed trades in the journal so behaviour patterns become queryable.
- Keep enrichment cost small enough to run on every item, every day.

## Non-goals and guiding principles

Jev never predicts prices or triggers orders. It judges text against criteria we write; its probabilities describe fit to those criteria, not odds that a trade makes money.

Non-goals:

- Price forecasting or buy/sell calls from raw price series.
- Replacing indicator math, date logic or options rules that code can compute exactly.
- Explaining decisions — Jev returns no reasoning ([tutorial FAQ](https://openrouter.ai/docs/guides/community/jev-tutorial)).

Principles:

1. Exact rules live in code. Moving averages, earnings-vs-expiry checks and position limits never go to Jev.
2. Jev can only filter or veto. It can remove a trade, never add one.
3. Store raw probabilities, not verdicts, so thresholds can be re-tuned without new calls.
4. Thresholds are named constants in code, tuned on labeled data and the cost of each mistake.
5. Any Jev failure fails closed: no answer means no new position.

## Architecture

Prices and text travel in separate lanes and meet only at the risk gate, so Jev can remove a setup but never create one.

&#91;embedded content: platform architecture · two input lanes into one risk gate\]

The enrichment worker runs asynchronously on each new text item and writes to `jev_answers`. The risk gate reads the latest stored answers when a setup fires, so a slow or failed Jev call never blocks the order path.

## Jev question catalog

Five questions cover the first release; the three news questions ride in one request per item because Jev answers all questions in a request in parallel.

| Question | Type | State sent | Answer options or levels | Used by |
| --- | --- | --- | --- | --- |
| `material` | Noul | Headline + first paragraph, ticker | Probability the item changes earnings, guidance or regulatory exposure | News filter |
| `catalyst` | Choice | Same as above | earnings, guidance, regulation/export controls, analyst action, M&A, noise | Feature, alert routing |
| `sentiment` | Score | Same as above | 5 levels: strongly negative → strongly positive for the ticker | Veto on long or short setups |
| `disclosure_signal` | Noul | Congressional trade disclosure record | Probability it is a meaningful new position, not a routine rebalance | Watchlist feed |
| `journal_tag` | Choice | Closed trade: plan, entry/exit notes, P&L | followed plan, chased entry, exited early, held too long | Weekly review |

Each question's `criteria` text is versioned in the repo with the thresholds tuned against it. A change to wording is treated as a new model version and re-evaluated.

Example request body for the news questions:

```json
{
  "model": "typesafe/jev-1.13",
  "state": {"ticker": "MU", "headline": "...", "body": "..."},
  "questions": {
    "material": {"type": "noul", "instructions": "Does this news materially affect the ticker's near-term outlook?",
      "criteria": {"true": "Changes earnings, guidance or regulatory exposure", "false": "Routine or recycled news"}},
    "sentiment": {"type": "score", "instructions": "How positive is this for the ticker?",
      "criteria": ["Strongly negative", "Negative", "Neutral", "Positive", "Strongly positive"]}
  }
}
```

## API integration

All calls go through one server-side `JevClient` wrapper that posts to OpenRouter's Decisions API and returns typed results or a fail-closed default.

- **Endpoint:** `POST https://openrouter.ai/api/alpha/decisions` with an OpenRouter API key as a Bearer token ([tutorial](https://openrouter.ai/docs/guides/community/jev-tutorial)). The endpoint is alpha, so the wrapper isolates the rest of the platform from schema changes.
- **Key handling:** the key lives in the secrets manager and is only read by the enrichment worker, never by UI code.
- **Versioning:** production pins `typesafe/jev-1.13`; `~typesafe/jev-latest` is used only in a shadow evaluation job. The response `model` field names the dated snapshot, and we store it with every answer.
- **Batching:** one request per text item, carrying every question about that item. Items are processed by a worker pool with a concurrency cap.
- **Retries:** retry transient errors (timeouts, 429, 5xx) with exponential backoff and jitter, up to 3 attempts. Validation errors are not retried; they are logged with the request hash.
- **Fallback:** after the last failure, the item is marked `jev_status = failed`. The risk gate treats failed or missing enrichment as a veto for new positions.
- **Idempotency:** each request is keyed by hash(item text + question set version + model id). A repeat item reuses the stored answer instead of calling again.
- **Context limit:** state + questions must fit 32,000 tokens; long filings are split into sections and each section is judged separately.

## Data model

Four tables keep raw text, Jev answers, price signals and final decisions separate, so every trade can be traced back to the exact inputs and model snapshot behind it.

| Table | Grain | Key columns | Notes |
| --- | --- | --- | --- |
| `raw_text_items` | One news item, filing section, disclosure or journal entry | item\_id, source, ticker, market (US/HK), published\_at, text\_hash, body | Append-only; dedupe on text\_hash |
| `jev_answers` | One question answered for one item | item\_id, question\_name, question\_set\_version, model\_snapshot, answer\_type, value (noul/score), choice, confidence, probabilities (VARIANT), cost\_usd, latency\_ms, jev\_status, created\_at | Raw probabilities stored; no thresholds applied here |
| `signals` | One setup per ticker per bar | signal\_id, ticker, bar\_ts, setup\_name, rule\_version, inputs (VARIANT) | Pure code output from price and volume |
| `trade_decisions` | One gate decision per signal | signal\_id, decision (take/skip/veto), veto\_reasons, threshold\_version, linked item\_ids, decided\_at | What the risk gate did and why |

A view joins `signals` to the latest `jev_answers` for the same ticker within a lookback window. The window length is a tunable constant.

## Thresholds, calibration and evaluation

Thresholds are set from a hand-labeled sample and from trade outcomes, never from round numbers. Jev's choice confidence summarises how concentrated the distribution is; it is not the probability of the winning option ([How to Use Jev](https://openrouter.ai/blog/tutorials/how-to-use-jev/)).

1. **Label a seed set.** Hand-label about 300–500 historical items per question (material yes/no, catalyst type, sentiment level), split across US and HK tickers.
2. **Measure agreement.** Run Jev over the set and compute precision/recall per question and per market. HK and any Chinese-language text get their own numbers before use.
3. **Pick thresholds by cost.** A missed material negative item on an open position costs more than a skipped setup, so the veto threshold favours recall.
4. **Outcome test.** Backtest the price-only strategy against price + Jev filters on the same period. Jev stays only if it improves risk-adjusted results out of sample.
5. **Monitor drift.** Track weekly distributions of each answer; a shift after a model snapshot change triggers a re-check against the labeled set.

Backfill caveat: historical news must be timestamped as first published, or the backtest leaks future information.

## Risk controls and failure modes

Hard limits sit in the risk gate, after Jev, and no model output can loosen them.

| Failure mode | Effect if unhandled | Control |
| --- | --- | --- |
| Jev timeout or API error | Setup trades with no news context | Fail closed: no new position; alert after repeated failures |
| Alpha API schema change | Parsing breaks or fields go missing | Wrapper validates responses; unknown shape = failed status |
| Model snapshot changes behaviour | Thresholds silently drift | Pin version; shadow-run the alias; drift monitor |
| Low confidence answer | Wrong veto or missed veto | Below-threshold items go to a manual review queue |
| Stale or duplicated news | Old items re-trigger vetoes | Dedupe on text hash; lookback window on published\_at |
| Misread HK or Chinese text | Wrong signals on HK names | HK enrichment off until evaluated separately |
| Over-trust of typed output | Jev treated as a trade signal | Principle 2: filter or veto only, enforced in code |

Position size, stop-loss, max daily loss and the earnings-vs-option-expiry check stay deterministic and are unit-tested.

## Cost and latency

Enrichment costs roughly $0.00002 per news item, so even 10,000 items a day stays near $6 a month. Jev bills input tokens only; output tokens are free ([Jev on OpenRouter](https://openrouter.ai/docs/guides/community/jev)). The tutorial's three-question request used 476 input tokens and cost $0.000019992 ([tutorial](https://openrouter.ai/docs/guides/community/jev-tutorial)).

| Daily items | Est. cost per day | Est. cost per month (30 days) |
| --- | --- | --- |
| 1,000 | $0.02 | $0.60 |
| 5,000 | $0.10 | $3.00 |
| 10,000 | $0.20 | $6.00 |

Estimates assume about 500 input tokens per item; long filings cost proportionally more. Every response carries `usage.cost`, which is stored in `jev_answers` so real spend replaces these estimates.

Latency: one community benchmark measured Jev's median about 3× faster than a frontier chat model on similar checks ([jev-test](https://github.com/souvikr/jev-test)). Our own p50/p95 are measured in the offline phase and set the worker timeout; enrichment runs off the order path, so latency affects freshness, not execution.

## Rollout plan

Four phases, each with an exit gate; real money is only reached after Jev shows value on paper.

1. **Offline evaluation.** Build `JevClient` and the tables, run the labeled seed set and a historical backfill.
   - Gate: per-question accuracy meets the target set in thresholds; measured p95 latency and cost logged.
2. **Alert-only.** Enrichment runs live on US news; answers appear on alerts but change nothing.
   - Gate: 2–4 weeks with no unhandled failures, and a manual review agrees with the vetoes it would have made.
3. **Paper trading.** The risk gate applies Jev vetoes to a paper account, run side by side with the price-only strategy.
   - Gate: the filtered strategy beats price-only on risk-adjusted return over the paper period.
4. **Limited live.** Vetoes apply to live trades at reduced position size, US tickers only.
   - Gate for HK: HK text passes its own offline evaluation before HK enrichment is switched on.

## Open questions

- [ ] Which news and filings source feeds `raw_text_items`, and does it cover HK names?
- [ ] Where does the platform store data today — Snowflake, Databricks or something else?
- [ ] Execution: alerts only, or a broker with an order API? Fidelity is assumed to have no retail trading API.
- [ ] Does Jev handle Chinese-language text well enough for HK tickers?
- [ ] Who labels the seed set, and how many items per question are enough?
- [ ] Lookback window for news vs. signals: same day, 24 hours, or since the last bar?
- [ ] Budget for the Decisions API being alpha: what if pricing or the endpoint changes?
