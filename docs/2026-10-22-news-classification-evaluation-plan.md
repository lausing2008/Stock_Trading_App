# News classification evaluation plan

Status: proposed, October 22, 2026. This document plans evaluation; it does not change classifiers, budgets, trading weights or live execution.

## Decision to resolve

Separate three claims: (1) the model correctly understands news; (2) those labels forecast market outcomes; (3) using those labels improves the app's decisions after costs. Passing one does not establish the next. The current 0–100 sentiment score is a tone scale, not a probability of profit or calibrated model confidence.

Observed implementation: `services/news-intelligence/src/services/classify.py` sends numbered headlines to Claude Haiku and requests sentiment, materiality and category. The request has no separate target-company context, article body, expectations, novelty or holding horizon. Storage can attach one article classification to multiple symbols. The signal engine's negative material-news flag compresses BUY-leaning scores for non-LONG horizons. The separate `news_sentiment` call site must be evaluated independently before measuring combined effects.

The October 22 30-day SWING/6-calendar-day filter audit has only five active hot-news observations among 433 evaluated surviving BUY signals. This is neither classifier accuracy nor a causal estimate of the gate's benefit. The API also incorrectly labels a zero-active-sample news-sentiment cohort “predictive.” Source: `docs/audits/2026-10-22-news-measurement-and-nu-cross.md`.

## Phase 1 — Freeze evidence and repair measurement semantics

Persist append-only classification records keyed by article version and target symbol. Record source/URL/content hash, event-cluster ID, publication time, first-seen time, content available at classification, request/response IDs, classifier/prompt/schema versions, raw parsed output, validation status, retry/deferral reason, completion time, tokens and actual cost. Keep secrets out of artifacts. Store the exact downstream policy and before/after score and action for every candidate, including blocked candidates. Separate news_classify and news_sentiment contributions.

Keep provider story revisions and cross-source duplicates in the same event cluster. A duplicate article or multiple ticker rows cannot inflate the independent sample size. Track entity relevance per ticker: the same merger may have different implications for acquirer and target.

Schema/parse errors and missing fields must be visible unknown/invalid states. The current parser defaults some missing/invalid outputs to neutral/other; test and remove that ambiguity before treating labels as ground truth. Sentiment 50 must never mean both genuinely neutral and failed classification. Test batch order/identity, missing and surplus outputs, mixed-symbol stories, empty/nonfinite scores, inconsistent score/label, adversarial headline instructions, duplicate updates and unavailable classification.

Output: versioned dataset manifest, error/latency/cost coverage report and an honest “insufficient evidence” state for unsupported performance claims. No verdict from a cohort with zero observations; descriptive effects with small n must not be presented as validated prediction.

## Phase 2 — Human reference dataset

Start with a 100-event pilot to refine the rubric. Build a proposed 1,000-event initial set: 600 development events and 400 locked, later-period test events. These are annotation-budget starting points, not statistical sufficiency guarantees. Reserve future events for prospective confirmation because pretrained models may have encountered historical stories.

Sample across source, US/HK, event category, company size/liquidity, positive/neutral/negative labels, material/nonmaterial labels, single/multiple companies, publication delay and deferred/error states. Enrich rare severe-negative events deliberately and retain selection probabilities; report production-weighted metrics separately from an unweighted stress set. Keep all articles/updates from an event in one partition, use a time gap for overlapping events, and report a held-out-symbol slice where coverage permits.

Two independent human reviewers label each event without seeing model outputs or subsequent prices. Adjudicate disagreements and keep uncertainty/disagreement fields. Another model may assist organization but cannot be the sole ground truth. Use only timestamped information available then. For headline-only evaluation allow “not determinable from headline”; richer-context adjudication uses only article text/context available at that time.

Per article-symbol labels:
- Entity relevance: direct, indirect/context, unrelated, uncertain.
- Event category with written definitions and examples.
- Fundamental/business implication: beneficial, adverse, mixed/neutral, unclear.
- Materiality: significant new information for this company, routine, unclear. Define this independently from subsequent price movement.
- Novelty: new event, meaningful update, duplicate/recap, unclear.
- Evidence sufficiency and known publication/ingestion delay.

Report macro-F1 and per-class precision/recall, confusion matrices, materiality recall and false-alarm rate, entity relevance, novelty/duplicate accuracy, unknown/abstention coverage and reviewer agreement. Show denominators and intervals clustered by event. Report severe-negative misses individually. If introducing a confidence field, calibrate it on development data and assess calibration/coverage on held-out data; do not repurpose sentiment as confidence.

## Phase 3 — Controlled classifier comparisons

Replay identical frozen inputs with four named variants:
1. Current headline-only model/prompt.
2. Cheap deterministic rules and category/majority baselines.
3. Same model with target-symbol context, publication time and a bounded timestamped article excerpt; require evidence and allow unknown.
4. A stronger model on exactly the same enriched inputs to isolate the model effect; separately test escalation only for ambiguous/high-impact items.

Freeze prompts and selection rules before opening the locked test. Measure quality, p50/p95 completion latency, tokens, actual cost per unique eligible event, duplicate work, failure and deferral rate. Repeated runs and batch reshuffling on a subset test label stability and positional errors. Restrict evaluation API spend with an explicit budget before execution; this plan does not authorize unlimited model calls.

Select a candidate on the development quality/cost tradeoff, then run one locked comparison. Report all variants; keep subsequent tuning out of that holdout. Use paired event-level comparisons. Larger models must earn their additional cost; label quality alone does not authorize a trading-policy change.

## Phase 4 — Prospective price and decision utility

Run shadow policies on the same pre-news candidate stream: news disabled, current news policy, and the selected candidate policy. Freeze every non-news input/weight, risk rule, entry/exit rule and cost model. Include candidates that become WAIT/HOLD or are blocked. Record exposure reduction and foregone winners as well as avoided losses. Separate deterministic per-candidate replay from a portfolio simulation with capital limits; duplicated counterfactual trades must not be counted as a realizable portfolio.

For event association, predeclare intraday horizons (30 minutes and session close) and swing horizons (1 and 5 trading sessions). For decision utility, retain the product's actual trading horizons and measure each separately. Entry must be the next executable quote/bar after classification completion plus delivery delay, never publication time or the same pre-decision candle close. Freeze market/sector benchmark mappings, corporate-action basis, calendars and price-source policy. Handle overnight events, trading halts, delistings, missing quotes, open outcomes and next-session gaps explicitly.

Report directional balanced accuracy and benchmark-relative returns for event associations. For policies report paired change in net return/expectancy, exposure-normalized return, maximum adverse excursion, drawdown/tail losses, missed profitable trades, turnover, abstention and costs/slippage sensitivity. Compute uncertainty with date/symbol/event dependence accounted for. Report multiple-testing treatment; do not select the best horizon after seeing outcomes.

Start with an 8-week shadow collection window, then assess information rather than declaring success by elapsed time. Predeclare the minimum economically meaningful benefit and use observed variability/clustering to estimate required independent event-days; continue if underpowered. Five triggered flags are insufficient. Negative results can mean “do not apply a trading gate” while retaining useful news organization.

## Release decisions and deliverables

1. Data integrity and reference-label agreement must be acceptable before model ranking.
2. A candidate must improve the predeclared classification objective against the current model/baselines on locked data, with acceptable severe-event miss rate, latency and cost. Set numerical tradeoffs after the blinded pilot, before candidate testing; do not invent a universal 90% accuracy target.
3. Trading influence requires separately demonstrated prospective policy benefit after costs and downside constraints. A confidence interval spanning meaningful harm and benefit means insufficient evidence. Monitoring/paper use can continue without asserting a trading edge.

Implement in this order: evidence tables/export and validation; annotation rubric and review/import workflow; offline evaluator with reproducible manifests and paired metrics; shadow candidate logging and matched outcomes; dashboard. Dashboard panels: label quality, pipeline coverage/latency/cost, and decision utility, each with model/policy version, window, n, independent-event count, uncertainty and data status.

Expected artifacts: versioned rubric, frozen input manifests, adjudicated labels, evaluator tests/reports, shadow policy specifications, maturation/coverage report, and promotion/rejection record. Model/unit/schema tests establish implementation correctness; human labels and prospective outcomes establish quality and utility.
