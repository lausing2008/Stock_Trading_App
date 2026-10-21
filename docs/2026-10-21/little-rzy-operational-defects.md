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

## Progress log

**2026-10-21 01:43 UTC — deployed `5e6410df` (market-data) and frontend `vm6Ic3QdsilXh1hX7ysV8`.** Both tiers stay open until a complete session shows bounded processing time, visible exclusions and materially fewer missed alerts.

- **Cached digest preview (OD-02 step 4) — done.** Each scan saves a per-stream summary (`little_rzy_stream_snapshots`); `/stocks/rzy/digest` assembles the preview from those rows only, with snapshot time, age, coverage and exclusions, or "Preview not available yet" when none exists. Production: 0.47 s with "not available yet" before the first snapshot (previously 504 after about 209 s). The scheduled 09:10 digest still computes live with the same rules.
- **Scan-run record (supports OD-02 step 5).** `little_rzy_scan_runs` stores one row per scan: duration, stream statuses including `restatement_pending`, emailable events, too-late-to-mail and queued counts.
- **Session report.** `scripts/rzy_session_report.py <day> <out>` separates completed-bar revisions from forming-bar updates, keeps `previous_provider_unknown` out of both hypotheses, gives median/p90/p95 over all emailable signals with expired/suppressed/too-late/paused counts, and writes raw CSVs plus a release/config/code manifest. Evidence is saved under `/home/ec2-user/rzy-session-evidence/<day>/`.
- **Smoke run on 2026-10-20** (trace tables empty that day; delivery path from existing timestamps): 5m had 11 emailable research signals — 4 provider-accepted (median 7.8 min after close), 3 expired, 4 recorded too late to queue; close → recorded median 4.8, p90 56.4, p95 61.4 min. 15m: 9 of 9 accepted, median 16.4 min after close. 1h: 3 of 3, median 18.7. Digest queued and accepted 13:12 UTC (09:12 EDT).

Provider policy (OD-01 step 2) waits for the trace evidence. RZY stays research-only while timing is unreliable.
