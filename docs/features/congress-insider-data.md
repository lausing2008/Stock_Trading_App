## Feature Reference: Congress Trading Data (Two Independent Implementations)

There are TWO separate, non-wire-compatible congress-trading code paths — this is intentional
duplication tracked as architectural debt (see `T233-ARCH-CONGRESS-DEDUP` in
`frontend/src/pages/improvements.tsx`), not a bug, but worth knowing both exist:

1. **`services/market-data/src/api/congress.py`** — `GET /congress/trades`. No DB persistence;
   live-fetches on every request from `_fetch_kadoa()` (or Quiver Quantitative if
   `quiver_api_key` is configured in Settings — richer metadata, $30/mo). Response is
   PascalCase (`Ticker`, `Date`, `Politician`, `Transaction`, `Min`, `Max`, `Party`, `State`,
   `Chamber`, `ReportDate`), binary `Purchase`/`Sale`/`Exchange` transaction type. Consumed by
   `frontend/src/pages/congress.tsx` and `frontend/src/pages/insider.tsx`.

2. **`services/event-intelligence/src/services/congress.py`** — `POST /events/sync/congress`
   (scheduled job) writes to the shared `congress_trades` DB table via
   `sync_congress_trades()`; `GET /events/congress/*` reads from it. Response is snake_case
   (`transaction_type`, `politician_name`, etc.), 4-state transaction type
   (purchase/sale/exchange/unknown), and feeds `compute_congress_score()` for catalyst scoring.
   Consumed by `frontend/src/pages/intelligence.tsx` and the catalyst-scoring pipeline.

Both now source from the same kadoa-org feed (see the Recurring Issue section above) but keep
independent parsing/schema — a fix to one's data source does NOT automatically fix the other;
they must each be checked/fixed separately, exactly as happened when the previous free source
died for both simultaneously.

---


---

## AUD-SMARTMONEY — "Who to Follow" Reports tab (Built 2026-09-22)

**Asked for as:** *"Create a report under report tab to trace/follow on some big guys trading
like Trump, Serenity with good performance. You can pick some of them for me as well."*

Lives at `/reports?tab=smartmoney` ("Who to Follow"), backed by
`get_smart_money_leaderboard()` in `services/event-intelligence/src/services/congress.py` and
`GET /events/congress/smart-money`.

### What it ranks, and why that is different from the tab next to it

`/events/congress/leaderboard` ranks **tickers** by congressional buying. This ranks **people**
by what following them would actually have paid. Both read `congress_trades`; they answer
different questions and neither substitutes for the other.

### The two facts that decide whether this report is honest

Both are enforced in code and rendered on the page, not buried in a footnote.

**1. Entry is the DISCLOSURE date, never the trade date.** Congressional filings lag the trade
by a **median of 40 days** (mean 76, worst 323). Measured on this platform's own data, the same
purchases return **+4.70% over 21 days from the trade date but +2.89% from the disclosure date**.
Roughly 1.8pp of the apparent edge has already happened before anyone outside could know. The
trade-date figure advertises a return the reader cannot reach — the same class of error as
quoting a post-earnings drift that includes the untradeable overnight gap (see
`docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md` for that parallel). Swapping the
correlated price subquery onto `trade_date` would raise every number on the page and *look like
an improvement*, which is why a test pins it.

**2. Most of the dataset has no buy/sell direction.** **7,691 of 9,453 rows** come from the
`unusual_whales` feed with `transaction_type = 'unknown'` — including **all 2,036 of Donald J
Trump's records**. You cannot follow a trade when you do not know which way it went; a filing
there may be a SALE. Only `kadoa_house` (944) and `kadoa_senate` (818) carry real
purchase/sale. Those rows are surfaced under `direction_unknown` rather than dropped, because
"tracked but not actionable" is a more useful statement than absence.

**This is why Trump — the single most active name in the table — cannot appear in the
followable leaderboard.** That is a data limitation, not an editorial choice, and the page says
so.

### Measured leaderboard (21-day forward, entered at disclosure, ≥8 buys on tracked stocks)

| trader | n | avg 21d | % up |
|---|---|---|---|
| Rohit Khanna (D, House) | 23 | **+5.26%** | 70% |
| Alan Armstrong (Senate) | 46 | **+4.70%** | 59% |
| Gilbert Cisneros (D, House) | 8 | **−5.18%** | 12% |

Only **3 of 30** returned names clear the floor. Cisneros is kept deliberately: a leaderboard
showing only winners hides the spread, and the spread is what tells a reader whether the top is
skill or the tail of a small sample. The below-floor names are shown behind a toggle rather than
truncated, for the same reason — seeing the leaders sit among dozens of 1–3 buy names is what
stops them being read as a ranking of skill.

### A data trap found in the live payload, documented on the page

**The two feeds do not share a name key.** The same person appears in both tables under
different spellings — `Rohit Khanna` (followable) vs `Ro Khanna` (direction_unknown);
`Gilbert Cisneros` vs `Hon. Gilbert Cisneros`; `Alan Armstrong` in both. The lists are **not
disjoint** and the filing counts **must not be added together**. Normalising the names was
deliberately not attempted — a wrong merge would silently pool two different people's returns,
which is worse than a stated caveat. If that is ever built, it needs a real identity mapping,
not fuzzy matching on a display string.

### "Serenity"

The user named Trump and "Serenity". **No entity matching "Serenity" exists** in
`congress_trades` or `institutional_holdings`. The institutional table is thin — Renaissance
Technologies (121 rows), ARK (51), Tiger Global (26), Pershing Square (5), Berkshire (4), 207
total — so an institutional version of this report is not currently supportable. Recorded here
so the question is not re-investigated from scratch.

### Tests

`services/event-intelligence/tests/test_smart_money_leaderboard.py` — 17 behavioural tests
against a stubbed session. sqlalchemy is stubbed suite-wide, so `text()` yields a MagicMock with
no readable SQL; the two query-shape assertions read the shipped source of that **one function**
via `inspect.getsource` (never the module) and pin no numeric literal — see
`AUD-T401-SOURCETEXTTESTS` in `docs/incidents/ci-failure-masking.md` for why that distinction is
load-bearing.

Sabotage-verified, each edit guarded by an `assert` that it actually applied (a sabotage silently
failing to apply already produced one false "pass" in this session):

| sabotage | result |
|---|---|
| entry basis → `trade_date` | 1 failed |
| sample floor → always adequate | 4 failed |
| `direction_unknown` folded into the ranked list | 1 failed |
| horizon header decoupled from the query binding | 1 failed |
| losing traders filtered out | 1 failed |

Restored: 506 passed.
