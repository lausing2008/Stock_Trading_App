# Ranking Engine — Domain Knowledge & Coding Standards

Computes K-score rankings and leaderboards for stocks across markets and sectors.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| K-score computation | `scoring/kscore.py` (~205 lines) |
| Ranking endpoints + leaderboards | `api/routes.py` (~788 lines) |

---

## K-Score

The K-score is a composite ranking metric (0–100) that aggregates:
- **Momentum**: price return vs sector over 5d, 20d, 60d windows
- **Technical quality**: RSI position, MACD alignment, Bollinger Band setup
- **Volume confirmation**: volume trend vs 20-day average
- **Signal strength**: signal confidence weighted by style alignment
- **Relative performance**: stock vs its sector ETF (sector relative strength)

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

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /rankings` | Yes | Full ranking list with K-scores |
| `GET /rankings/top?market=US&n=20` | Yes | Top N stocks by K-score |
| `GET /rankings/sector/{sector}` | Yes | Rankings within a sector |
| `GET /rankings/{symbol}` | Yes | Single symbol's rank and K-score |

---

## Scheduler Integration

Rankings are recomputed 5× per market day by the market-data scheduler.
The scheduler calls `POST /rankings/refresh` with a service token.
Rankings are cached in Redis between refreshes.

---

## Dependencies

Rankings require prices and TA data — the ranking engine calls:
- `GET /stocks/{symbol}` (market-data) for price data
- `GET /ta/{symbol}` (technical-analysis) for RSI, MACD, BB

If either upstream is unavailable, the ranking engine gracefully returns the last cached ranking.
