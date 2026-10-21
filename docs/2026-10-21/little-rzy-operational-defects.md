# Little RZY operational defects — tracked separately from the audit

Found while verifying the [audit resolution](../2026-10-20/little-rzy-audit-resolution.md) in production. Trading rules are unchanged. The immediate milestone is **dependable, measurable research alerts**; paper execution comes after that. Each fix below is its own reviewed change.

## OD-01 — completed intraday bars are restated (tracker 457, in progress)

**Evidence (2026-10-20 session):** 3,956 Little RZY rebuilds for `stored_completed_bar_changed` across 314 streams; changes median 0.077% (p90 0.50%), usually all four OHLC fields, mostly on bars 1–6 h old. The rebuild cap (one per stream per 30 min) contains load but can pause a stream for up to 30 minutes, which makes 5m alerts unsuitable for timely execution.

**Hypothesis (unconfirmed):** US 5m routing is unusual_whales → yfinance → polygon and every intraday fetch re-upserts the whole day, so fallback fetches from a different vendor may overwrite completed bars. Corrections within one provider are the alternative explanation; both may occur.

**Plan:**
1. **Trace bar revisions — deployed for the 2026-10-21 session.** `bar_provenance` records the provider that last wrote each intraday bar (first/last write, stored values); `bar_revisions` records each OHLC change beyond 1e-6 with candle time, fetch time, bar age, previous/new provider, old/new OHLCV, changed fields, size and class `cross_provider` / `same_provider` / `previous_provider_unknown` (suffix `_forming_bar` while the candle is still open). Volume-only changes and same-value provider switches are counted. Observation only: written values are unchanged, and a trace failure is isolated in a SAVEPOINT. Report: `scripts/rzy_session_report.py <date>` (read-only).
2. **Source policy** — after the evidence: prevent silent cross-provider replacement of completed bars; preserve legitimate corrections explicitly; never freeze incorrect prices just to avoid rebuilds.

Note for reading day one: bars written before the trace was deployed have no provenance, so their first revision is `previous_provider_unknown`; by the second fetch of a session every bar has a recorded provider.

## OD-02 — alert latency and digest preview cost (tracker 458, open)

**Evidence (2026-10-20):** 5m events recorded a median 4.8 min after candle close (p90 16.4, max 56.4); 320 of 1,072 (30%) were outside the email freshness window and were not mailed. The bulk digest preview took 209 s (load_bars 141 s) and returns 504 at the gateway. The digest itself was provider-accepted at 09:12 EDT, two minutes after its 09:10 schedule.

**Plan:**
3. Reduce scan work: incremental reads with enough indicator/pivot history and detection of older corrections; compare against full replay before deployment.
4. Digest preview reads cached results and shows snapshot time and freshness instead of computing during the request.
5. Measure the complete delivery path — candle close → ingestion → evaluation → queue → provider acceptance — plus suppressed signals and paused streams. `rzy_session_report.py` measures these stages from existing timestamps starting 2026-10-21. Provider acceptance is not confirmed inbox delivery.
