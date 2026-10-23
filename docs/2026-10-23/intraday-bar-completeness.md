# Intraday 5m candle completeness (tracker 457, three separate defects)

**Status (2026-10-23 00:50 ET):** fixes 1 and 2 **deployed** (`a1de7cbf`, 04:35 UTC); each stays open until production evidence (below). Item 3 is classified only. RZY's data-readiness gate stays **open** until the liquid-symbol defects are fixed and verified in production and the sparse intervals are classified.

**Measure.** Completeness is the set of **expected grid timestamps** present (`session_grid.expected_grid`, from the exchange calendar: 78 five-minute candles on a regular US day, 42 on an NYSE early close, 66 in HK with the lunch break). Off-grid rows are counted separately. Row counts are not used: 77 valid candles plus 2 snapshots is 79 rows and still missing a candle.

## 1. Missing final candle (liquid symbols)

**Trace (2026-10-22 session):**

| Stage | Evidence |
|---|---|
| Scheduler | `us_5m_intraday` fires at hours 9–15, so its last run is **15:55 ET**. The 15:55 candle completes at 16:00. The close-burst and 16:30 post-close jobs ingest **daily** bars only. Nothing ingests 5m until the next morning. HK has the same shape. |
| Database writes | **No 5m write after 15:55:55 ET for any symbol.** GDX, MDB, SMH, SOXX and VICR were last written at 15:55:01–15:55:03, before the candle existed, so they have no 15:55 candle. AAPL, MU and NVDA were written at 15:55:25–35, so their stored final candle is a **seconds-old forming snapshot**. 0 of 137 stored 15:55 candles were written after completion. |
| Provider | Unusual Whales (the authority) **now returns** all 78 on-grid candles for SMH, GDX, MDB and VOO, including 15:55. Whether it had the candle at 15:55:01 cannot be shown, and is moot: no fetch ran after completion. |
| Authority | **Not established as a cause.** No write was attempted after completion, so no authority decision was made. |
| History | On Oct 20 and 21 the final candles were completed only by the **next morning's** run (last writes 10:00 and 05:30 ET the following day). So every evening until the next morning, the final candle is missing or partial. |

**Fix (built):** `_reconcile_session_5m` runs at 16:02 and 16:07 local (US and HK), plus 12:02/12:07 HKT for the morning session. It is ingest only, through the normal path, so bar authority applies:
- the authority completes its own forming snapshot;
- a missing candle is inserted;
- no other provider overwrites an accepted candle.

It runs no trading step and no RZY scan. The RZY scan's own window (open → close + 5 min) and freshness rules decide mailability, so a candle reconciled later is history, not an alert. It records grid completeness per symbol, lists final candles still missing, and sets `partial` when any remain.

**Acceptance:**
- PostgreSQL probe: a forming final snapshot is completed by the authority after the bell and a later fallback is refused; a missing final candle is inserted and then owned.
- Unit tests for the grid (DST, early close, holiday, HK lunch, the midday `until`) and for the job (ingest only, completeness detail, status).
- In production: after the next session, 0 symbols with a missing or forming final candle at 16:10 ET.

## 2. Off-grid "opening snapshot" rows

**Writer:** the **yfinance adapter**, which requests history with pre/post-market data and appends yfinance's current-quote row. Every off-grid 5m row on 2026-10-22 came from yfinance:
- 894 rows: 868 in premarket, 21 in the regular session, 5 in HK;
- open = high = low = close, volume 0, stamped at the last trade (09:30:01, 01:54, 08:39:54 …);
- hundreds to more than 2,000 such rows per day since Oct 1.

RZY already excluded them; charts and other 5m consumers did not.

**Fix (built):** ingestion drops off-grid intraday rows before the authority check and the write (`exclude_off_grid_snapshots`, the same rule as the provenance trace's `off_grid`). **Timestamps are never rounded,** so the genuine 09:30 candle cannot be overwritten. Daily and weekly rows are untouched. The count is logged as `ingest.off_grid_snapshots_excluded`.

**Existing rows:** to be **exported with provenance first** (symbol, ts, OHLCV, session, provider, write times) and only then deleted, as a separate reviewed step after the fix is deployed.

## 3. Sparse-symbol intervals

Each missing grid interval (excluding the item 1 final candle) was compared with both providers' current responses:

| Class | Oct 21 | Oct 22 | Meaning |
|---|---|---|---|
| provider_unavailable | 78 | 77 | Neither provider serves the session (SSNLF) |
| unknown_interval_omitted | 188 | 156 | Both providers serve the session but neither has the interval: probable no-trade, **not confirmed** (no trade-level evidence) |
| ingestion_failure | 0 | 7 | A provider has the candle now and we do not (XSD 3, BRK-A 2, AMADY 1, EE 1). Expected to be recovered by the reconciliation or next-morning fetch; to be rechecked |
| confirmed_no_trade | 0 | 0 | Not claimed without trade-level evidence |

Unusual Whales has no session data for AMADY and returns an error for BRK-A; yfinance serves both. No candles are manufactured.

## Still separate

The option-chain archive gaps, the backup restore test and the Friday snapshot-run verification are tracked on their own.

## Deployment and verification (2026-10-23)

- **Deploy:** market-data `a1de7cbf` built from the reviewed commit at Fri 00:35 ET. Logs saved (`~/container-logs/`); `rollback-before-a1de7cbf3` kept; drift check 12/12 clean.
- **Reconciliation, one-off run for session Thu 2026-10-22** (clock set to 16:02 ET; outside the RZY scan window, so no alerts):
  - 143/157 complete;
  - final candle missing only for AMADY and AMZA (thin, item 3);
  - no forming final candles;
  - no session data for SSNLF, SKHYV and US10Y.

  **The scheduled run for session Fri 2026-10-23 (16:02 ET) is the acceptance check for item 1.**
- **Off-grid cleanup:**
  - **Export:** 127,027 rows with id, values and provenance, at `EC2:~/offgrid-export-2026-10-23/` and `~/Documents/MyProjects/incident-evidence/offgrid-export-2026-10-23/` (sha256 `05171ebb…`). All rows are snapshot-shaped (volume 0, O=H=L=C). Provenance exists for the 941 rows written since the trace started.
  - **Delete:** one transaction removed only exported ids whose stored values still matched: **127,025 rows**. The 2 HK rows in the 16:00–16:15 HKT closing-auction window were kept.
  - **After:** 0 off-grid rows in the US Oct 20–22 and HK Oct 21–22 sessions; grid counts unchanged.
- **RZY:** no rebuilds followed (RZY already excluded these rows). Check again after its pre-open maintenance. Rebuilds never re-send an occurrence's email.
- **Item 2's acceptance:** the off-grid exclusion counter after the 04:00 ET yfinance premarket run.
