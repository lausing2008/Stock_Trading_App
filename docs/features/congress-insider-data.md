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

---

## AUD-UWCONGRESS-FIELDNAMES — 81% of the dataset was discarded at parse time (2026-09-23)

**This entry corrects the numbers in the AUD-SMARTMONEY entry above.** Read this one.

### The bug

`shared/common/uw_congress.py` probed for `transaction_type`, `filing_date`, `amount_min` and
`chamber`. Unusual Whales' `/api/congress/recent-trades` sends `txn_type`, `filed_at_date`,
`amounts` and `member_type`. **Not one key matched.** Every field fell through to its default, so
**7,691 of 9,453 stored rows (81%)** had no direction, no disclosure date, no amount and no party
— including all 2,036 rows for Donald J Trump, which is the actual reason he could not appear in
the follow-return leaderboard.

Nothing was ever missing from the feed. The rows arrived complete and were dropped on the way in.

### Why it survived so long

`row.get("transaction_type")` on a dict without that key is not an error — it is `None`, which
`_normalize_congress_txn_type()` faithfully converts to the legitimate-looking value `"unknown"`.
Every layer above behaved correctly on what it was handed. And `transaction_date` — the single
key that happened to line up — kept `trade_date` populated, so the result looked like a *sparse
feed* rather than a *mapping error*.

The mapping also lived inside the HTTP call, so no test could reach it without a live request.
`_parse_congress_rows()` was extracted specifically to end that.

### The field map (verified against a live payload AND the published OpenAPI spec)

| code probed | UW actually sends | effect |
|---|---|---|
| `transaction_type`, `type` | **`txn_type`** | always `"unknown"` |
| `filing_date`, `disclosure_date` | **`filed_at_date`** | `disclosure_date` always NULL |
| `amount_min` / `amount_max` | **`amounts`** (`"$15,001 - $50,000"`) | both always NULL |
| `chamber` | **`member_type`** (`house`/`senate`/`executive`) | always NULL |
| prefers `reporter` | `reporter` is the honorific form (184/200 rows) | split one person's identity |

`txn_type` has **nine** spellings with inconsistent casing: `Buy`, `Purchase`, `Sell`,
`Sell (partial)`, `Sell (PARTIAL)`, `Sale (Partial)`, `Sale (Full)`, `Exchange`, `Receive`.
`Receive` is a transfer in, not an open-market buy; it maps to `exchange` rather than falling
through to `raw[:32]`, which would put the literal string `"receive"` into a vocabulary three
other functions match against.

**Party** is genuinely absent from UW's trade rows — it exists only on
`/api/congress/politicians` (408 members), now fetched by `get_congress_roster()` and joined by
name, with `"democrat"` normalised to the `D`/`R`/`I` the kadoa rows already used.

### AUD-UWCONGRESS-NAMEMERGE — the defect the repair exposed

With the data fixed, one member ranked as **two traders with contradictory returns**: Khanna at
+5.26% (n=23) and +3.30% (n=55). `canonicalize_politician_name()` maps a feed's spelling onto the
roster's, and is deliberately conservative — surname must match **in the same chamber**, given
names must be prefix-compatible, and **exactly one** candidate may survive. Order matters:
filtering on surname uniqueness first refuses `David J. Taylor` merely because a Nicholas Taylor
also sits in the House. No roster means no canonicalisation at all.

`scripts/migrate_canonicalize_congress_names.py` repaired the stored rows: 18 renames, 216
duplicate rows removed, 371 renamed, 186 → 163 distinct names.

**A stale Redis cache nearly made that migration a silent no-op.** The roster is cached 24h; a
cache written before `canonical_name` existed returned entries without it, so every rename
resolved to nothing and the dry run reported `WOULD RENAME: 0` against data with 18 pending. The
script now aborts loudly instead of succeeding at nothing.

### Corrected measurements — the old figures were computed on 19% of the data

| | before (19% of rows) | after |
|---|---|---|
| disclosure lag, median | 40 d | **33 d** (mean 76 → 45; worst 323 both) |
| entry at trade date | +4.70% | **+3.73%** (n=704) |
| entry at disclosure date | +2.89% | **+3.15%** (n=622) |
| cost of the lag | 1.81 pp | **0.58 pp** |
| followable traders | 3 | **17** |
| Gilbert Cisneros | −5.18% (n=8) | **+5.66% (n=107)** — a full sign flip |

The **direction** of the lag effect survived; its **size** did not. Cisneros had been shipped as
the page's negative contrast on a sample that then quintupled and reversed. A figure measured on
a fraction of the data is not a smaller version of the truth — it is a different number.

**Trump is now rankable: n=73 buys, −0.63%, 42% up, `chamber = Executive`** (UW's `member_type`
confirms executive branch, not Congress). His holdings are a broad blue-chip basket, which looks
like a managed mandate rather than stock-picking — worth stating before anyone reads him as a
trader.

**776 rows still carry no direction** and remain in `direction_unknown`; they were not superseded
by the re-ingest, so they were deliberately kept rather than deleted. That count is now computed
by the API instead of written into the caveat text — the old string said "7,691 of 9,453" and
would have gone on saying it after the repair made it 776.

### Testing note worth keeping

Of the first five name-merge sabotages, **three passed unnoticed — because the FIXTURES could not
express the failure**, not because the code was right: no two given-name-compatible candidates
existed, the chamber guard was unreachable, and `toks[1] == toks[-1]` for every two-token name.
Each gap was closed with a real case (`Jo` against both a John and a Joseph; `Marjorie T. Greene`;
`Angus S King, Jr.`) and a fixture whose canonical spelling *differs* from its input, so a wrong
merge is distinguishable from no merge.

**A test passing under sabotage is a statement about the test, not the code.**

Separately, an early sabotage round reported the *restored* code as failing: rapid `cp` restores
left **stale `__pycache__`**, so what executed was not what `inspect` showed. Every sabotage loop
now clears bytecode first. Unnoticed, that would have let a sabotage report "caught" while
verifying nothing.

### Sources evaluated and rejected (2026-09-23)

- **open-cabinet.org** — real, live, MIT, 33,964 rows, 99.99% direction. But **executive branch,
  not Congress**, has **no disclosure-date column**, and only ~23% of rows carry a ticker.
- **trumptracker.org** — tracks *cabinet officials*; "Latest Trades" reads "No stock trades
  reported yet". No API or export.
- **House Clerk bulk ZIP** — the XML is a filing **index only** (9 fields, no ticker/type/amount).
  Detail is in per-filing PDFs; ~88.5% are text-extractable, 11.5% scanned.
- **House/Senate Stock Watcher** — **NXDOMAIN, permanently dead.** The URLs are still in
  `congress.py:29-30` and should be deleted.
- **Legal flag:** the Senate eFD gate quotes **5 U.S.C. app. § 105(c)** — unlawful to use these
  reports "for any commercial purpose", up to $10,000 per action, and it covers House filings
  too. Routing through a vendor who has priced that risk in (UW) is safer than scraping .gov.
  Quiver's $30/$75 tiers grant **no** commercial rights; that begins at $125/mo.
- **13F institutional** — quarter-end snapshot, +45 days to file, ~2 weeks to publish:
  **45–135 days stale**, no intra-quarter round trips, no shorts. Not a timing instrument.
- **SEC Form 4** — **2 business days**, explicit direction, free, ~850 filings/day. UW also
  serves it at `/api/insider/transactions` with an `is_10b5_1` flag that separates discretionary
  buys from pre-scheduled plan sales. The strongest available "informed money" signal.

---

## AUD-INSIDERUW / AUD-INSIDERROLE — SEC Form 4 (2026-09-24)

### Measured before anything was built

The 147 open-market purchases the EDGAR scrape accumulated over two years:

| | |
|---|---|
| mean 21-day alpha vs SPY | **+0.69%** |
| beat rate | 58.0% (n=112 resolved) |
| t, naive | 0.93 |
| **t, day-clustered** (46 filing days) | **0.60** |
| sd | 7.85% |

**`|t| < 2` means NOT YET MEASURABLE, never "no edge."** The standard deviation is more than ten
times the mean, so what is missing is *sample*, not signal. **No signal, alert or tab was shipped
on t = 0.60** — the work was to make the question answerable, not to answer it prematurely.

### …and that +0.69% is an upper bound, because the entry date was unreachable

The EDGAR parser never set a real filing date. It copied the transaction date:

```python
"filing_date": txn_date,  # approximate — actual filing date from index
```

**All 1,049 stored rows had `filing_date == transaction_date`, average lag 0.00 days.** Form 4 is
filed up to *two business days* after the trade, so every return measured from that column was
entered on a date nobody outside the company could act on.

This is the **third instance of the same error found in one session** — after the congressional
trade-date figure (+4.70% unreachable vs +3.15% real) and post-earnings drift that includes the
overnight gap. The pattern is worth naming: *whenever a return looks good, check which date it
was entered on.*

### What the UW path adds

Not freshness — nothing is faster than the SEC's own filing system, and EDGAR stays primary.

| | EDGAR path | UW `/api/insider/transactions` |
|---|---|---|
| real filing date | **none** (copies transaction date) | both dates (sampled: traded 09-20, filed 09-22) |
| `is_10b5_1` populated | 11 of 1,049 | **500 of 500** |
| role | `"1"` / `"true"` (see below) | clean `officer_title` + explicit flags |

`is_10b5_1` is the **entire signal/noise line** for insider activity: a sale scheduled six months
ago reveals nothing about anyone's view today.

**Only `P` and `S` are stored**, matching the EDGAR path's own filter. In a real 500-row sample
`P` was 27 rows against 261 awards / option exercises / tax-withholding disposals — storing
compensation mechanics as decisions would bury the signal under ten times its own volume.

UW returns no SEC accession number and that column is the table's unique key, so a synthetic id
is hashed over the identifying fields and namespaced `uw:` — re-runs are idempotent and the two
sources cannot double-insert the same event.

Job runs on the same 4-hourly cadence, **offset 30 minutes** so the two never contend for the
same session pool (`AUD-CONNPOOL-NESTEDSESSION` is this repo's reminder of what that costs).

First live run: 500 fetched → 30 stored, 279 skipped as non-open-market, 191 as untracked
tickers. `is_10b5_1` coverage 11 → 209; 13 rows now carry a real filing lag where none did.

### AUD-INSIDERROLE — a boolean stored as a job title

```python
role_raw = _tag("officerTitle") or _tag("isDirector") or ""
```

`isDirector` is a **boolean** Form 4 tag valued `"1"`/`"true"`. Any director filing without an
officer title had the literal string `"1"` stored as their role — **254 of 1,049 rows** read
`"1"` (180) or `"true"` (74).

Fixed by resolving the flags into real names. Backfilled: 254 → `Director`; a further 13 whose
flag was `"0"`/`"false"` (no title *and* not a director, so the role is genuinely unknown) →
`Insider`, which states that rather than inventing a title.

### Testing note

19 tests, 9 sabotages. Two harness defects, both worth remembering:

- **A signed `amount` stored as a negative share count** — silently flipping every `total_value`
  that multiplies by it — passed the first round because nothing asserted on what was *written*.
  `pg_insert` is a MagicMock under this suite's sqlalchemy stub, so the values were unreachable
  until the harness recorded them.
- **The fake session had to dispatch on CALL ORDER**, not by inspecting the statement: a
  MagicMock auto-creates any attribute as truthy, so `getattr(stmt, "_is_select_stub", False)`
  matched every insert and the harness silently reported zero rows stored.
