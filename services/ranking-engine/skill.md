# Ranking Engine — Domain Knowledge & Coding Standards

Computes K-score rankings and leaderboards for stocks across markets and sectors.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| K-score computation | `scoring/kscore.py` (~205 lines) |
| Ranking endpoints + leaderboards | `api/routes.py` (~838 lines) |

---

## K-Score

The K-score is a composite ranking metric (0–100) computed by `compute_kscore()` from a weighted
sum of 6 components (see `_WEIGHTS` dict in `kscore.py`) — **not** the momentum/TA/volume/signal
list previously documented here, which didn't match the actual code:
- **`technical`** (weight 0.22) — computed independently inside kscore.py (own RSI/ADX
  implementation), NOT fetched from the technical-analysis service
- **`momentum`** (weight 0.23) — price return vs sector
- **`value`** (weight 0.13) — fundamentals-based value score (or price-proxy fallback when
  fundamentals are unavailable — this is legitimately `None` for some stocks, see the
  T232-RANKSTALE-SCHEMA nullable-columns fix in the improvement tracker)
- **`growth`** (weight 0.14) — fundamentals-based growth score (same None-fallback caveat)
- **`volatility`** (weight 0.18) — inverse volatility scoring
- **`relative_strength`** (weight 0.10) — stock vs its own benchmark (HK: whole HSI index; US:
  sector ETF — these are methodologically different benchmarks per market, not the same metric)

**Ranking-engine does NOT call the technical-analysis service for any of these components.**
The only HTTP call to technical-analysis is `_fetch_patterns_bulk()` → `/ta/patterns/bulk`,
which only attaches a cosmetic `patterns` list to the leaderboard display — it is never an
input to `compute_kscore()`. If you need the real RSI/momentum/technical-quality inputs, read
`kscore.py`'s own `_rsi()`/`_adx_value()`/`_technical_score()` functions directly.

Score interpretation:
- 80–100: Strong momentum, technically sound, high relative strength
- 60–80: Good setup, moderate confirmation
- 40–60: Neutral — neither strong buy nor sell signal
- < 40: Weak momentum, technical deterioration

### K-score vs signal confidence
K-score ranks stocks against each other. Signal confidence measures absolute conviction.
A stock can have K-score=85 (best in sector) but HOLD signal (absolute threshold not met).
The rankings page uses K-score; the signal filter uses signal direction + confidence.

---

## Leaderboard Endpoints

Corrected 2026-07-04 — the previous table named endpoints that don't exist (`/rankings/top`,
`/rankings/sector/{sector}`) and claimed auth on endpoints that are actually public:

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /rankings` | **No** | Full leaderboard with K-scores (cached/persisted) |
| `GET /rankings/{symbol}` | **No** | Single symbol's live-computed rank and K-score |
| `GET /rankings/screen` | **No** | Screener — filter/sort the universe by K-score criteria |
| `GET /rankings/sector_rotation` | **No** | Sector-level rotation view |
| `POST /rankings/refresh` | **Yes** | Bulk recompute + persist rankings for a market — the ONLY authenticated endpoint in this service |

Only `/rankings/refresh` requires a JWT. Every read endpoint is intentionally public (mirrors
signal-engine's `GET /signals/{symbol}` pattern — cheap, read-heavy, no sensitive data).

---

## Scheduler Integration

Rankings are recomputed 5× per market day by the market-data scheduler.
The scheduler calls `POST /rankings/refresh` with a service token.
Rankings are cached in Redis between refreshes.

---

## Dependencies

Rankings require prices from market-data (read directly via shared DB session, not HTTP) and
sector ETF data for relative-strength. Ranking-engine does **not** call the technical-analysis
service for K-score inputs (see K-Score section above) — it computes RSI/momentum itself.
The only real HTTP dependency is `_fetch_patterns_bulk()` → technical-analysis `/ta/patterns/bulk`
for a cosmetic leaderboard column; if that call fails, a stale in-process cache (6h TTL, resets
on container restart, not shared across replicas) is served with no staleness indicator.

## Recurring Issue: Silent Background-Task Failure (fixed 2026-07-02, T232-RANKSTALE)

Rankings went stale for 10+ days in production with zero visible errors. Two compounding bugs
in `_persist_rankings()`:
1. The INSERT/commit block was indented OUTSIDE the `if rows:` guard — when `rows` was empty
   (e.g. because every stock in a batch errored), `stmt` was undefined and raised a bare
   `NameError`, silently killing the whole background task (FastAPI `BackgroundTasks` exceptions
   are never surfaced to any caller).
2. Root numeric cause: `rankings.value`/`rankings.growth` were `NOT NULL` in the schema, but
   `compute_kscore()` legitimately returns `None` for stocks lacking full fundamentals — every
   batch containing such a stock failed the whole multi-row INSERT with a `NotNullViolation`,
   which fed directly into bug #1's empty-`rows` NameError.

**Fix:** made `value`/`growth` nullable (matches `KScoreComponents`'s own `float | None` type),
added structured logging (`ranking.persist_rankings_started/done/failed`, per-stock failure
counts) and per-stock exception isolation so one bad stock can't kill the whole batch.

**Design invariant going forward:** any function running as a `BackgroundTasks` callback (see
`POST /rankings/refresh`) MUST wrap its entire body in try/except with a log line on both success
and failure — a naked background task that silently NameErrors on rare input is invisible for as
long as nobody happens to check `MAX(as_of)` on the `rankings` table by hand. Also: a data-quality
check (`rankings_us`/`rankings_hk` in `_DQ_CHECKS`, `scheduler.py`, checked every 2h) now catches
this class of staleness automatically going forward — see `T232-DQ-FRAMEWORK`.
