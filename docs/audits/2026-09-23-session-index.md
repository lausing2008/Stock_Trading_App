# Session Index — 2026-09-23/24: "who are the big traders, and are these alerts any good?"

**START HERE** for this session. 18 commits, 37 files, ~7,670 insertions. Everything deployed
with 0 drift; all backend suites green (market-data 4,260 · event-intelligence 562 ·
signal-engine 549 · api-gateway 54 · news-intelligence 89).

The session began as "build a report tracking notable traders" and turned into something else
twice: first a **parse bug that was discarding 81% of the congress dataset on arrival**, then a
**measurement of the alert emails** that found the only BUY-direction alert is anti-predictive.

---

## The finding that reframed the session

Not that congressional data was thin — that **we were throwing most of it away.**

`shared/common/uw_congress.py` probed for `transaction_type`, `filing_date`, `amount_min` and
`chamber`. Unusual Whales sends `txn_type`, `filed_at_date`, `amounts` and `member_type`.
**Not one key matched.** 7,691 of 9,453 rows (81%) had no direction, no disclosure date, no
amount and no party — including all 2,036 rows for the most active filer on the platform.

**Why it was invisible:** `row.get("transaction_type")` on a dict without that key is not an
error. It is `None`, which the normaliser faithfully turns into the legitimate-looking value
`"unknown"`. And `transaction_date` — the one key that *did* line up — kept `trade_date`
populated, so the result read as a **sparse feed** rather than a **mapping error**.

Repairing four dictionary keys took the followable leaderboard from **3 traders to 17** and made
Trump rankable for the first time.

---

## BUILT

### Who to Follow — congressional disclosures ranked by person

| | commit |
|---|---|
| `AUD-SMARTMONEY` — `get_smart_money_leaderboard()`, entered at the **disclosure** date | `b693497` |
| The Reports tab, 17 tests, 5 sabotages | `9d24372` |
| `AUD-UWCONGRESS-FIELDNAMES` — the parse bug above; roster join for `party`; `_parse_congress_rows()` extracted so the mapping is reachable by a test at all | `61bf0d2` |
| `AUD-UWCONGRESS-NAMEMERGE` — one member was ranking as **two traders with contradictory returns** (Khanna at +5.26%/n=23 and +3.30%/n=55) | `48edf67` |
| One-time canonicalisation migration — 18 renames, 216 duplicate rows removed, 186 → 163 distinct names | `dff9220` |
| Corrected every figure the tab had shipped | `191e065` |

Full detail: `docs/features/congress-insider-data.md`.

### Big Funds — 13F position adds for 16 named managers

| | commit |
|---|---|
| `AUD-INSTFOLLOW` — `/api/institution/{name}/activity`, entered at the **filing** date, ranked on alpha | `2164e52` |

Full detail: `docs/features/options-and-institutional-data.md`.

### The alert emails

| | commit |
|---|---|
| `AUD-SQUEEZE-ENTRYGAP` — the measured gap between the alert price and the next session's entry | `6694102` |
| `AUD-ALERTPREFS` — per-alert-type preferences, HMAC one-click unsubscribe, settings screen | `ef3c5f1`, `3d9fbb3` |
| `AUD-SIGNALCOHORT` — the AI Signal email now states the base rate for its own direction+horizon | `f7e9fea` |

Full detail: `docs/features/squeeze-and-options-alerts.md`, `docs/features/admin-and-settings.md`,
`docs/features/signal-engine-pillars.md`.

---

## What the measurements actually said

Unflattering, and recorded because that is the point of measuring.

**The Short Squeeze Alert — the only BUY-direction alert of the four — is anti-predictive.**
13.3% win rate over 15 resolved alerts, −5.1% average at 5 days (p≈0.004 against a coin flip).
Root cause: it fires on an intraday move already **≥3%**, and a **median −6.65%** is given back
before anyone can enter (12 of 15 adverse). From the alert price the 5-day return is **−10.6%**.

The contrast that makes the diagnosis convincing: **Squeeze Watch — the earlier, explicitly
lower-confidence alert — has a POSITIVE entry gap (+1.36%)**. Better execution on the weaker
signal is exactly what a chase detector looks like.

**The other three have no measurable edge.** Options Expiry Watch is a coin flip with a
genuinely zero average return on the largest sample (370 alerts); Pre-Breakout shows nothing
(n=36); Squeeze Watch has too few resolved to judge.

**The AI Signal badge was pooling opposites.** BUY averages **−1.22%** over 5 days (n=13,553),
SELL **+1.03%** (n=5,008) — so a per-symbol win rate mixing them describes neither.

**Congressional disclosure lag costs about 0.58pp**, not the 1.81pp first reported — the earlier
figure came from the 19% of rows that then had a usable disclosure date.

---

## NOT BUILT — and why

**Short-interest staleness cannot be fixed at the source.** UW's `market_date` is **identical**
to Yahoo's (both 2026-08-31); they derive from the same FINRA settlement, published bi-monthly.
No vendor is fresher. The remaining exposure is *scheduling*: a 30-day hard cutoff against a
**weekly** fundamentals refresh can drift to 28 days. Scoped, deliberately not bolted onto this
session's deploys.

**Recipient symbol-matching was NOT added.** `AUD-ALERTPREFS` fixed *which types* you receive,
not the fact that an alert on any symbol reaches anyone holding any untriggered `PriceAlert`.
Matching candidates against the user's own symbols is a separate decision about who gets mail.

**A per-stock earnings-drift rate, the HMM bear gate, and the ranking rebuild** all remain
rejected/blocked on the evidence recorded in `docs/audits/2026-09-22-session-index.md`.

**SEC Form 4 insiders — the strongest unbuilt item.** 2-business-day lag versus Congress's 40,
explicit direction, free, ~850 filings/day, and UW already serves it at
`/api/insider/transactions` with an `is_10b5_1` flag separating discretionary buys from
pre-scheduled plan sales. This is the natural next build.

**External sources evaluated and declined:** open-cabinet.org (executive branch, no disclosure
date, 23% ticker coverage), trumptracker.org (cabinet officials, "no stock trades reported yet",
no API), House Clerk bulk ZIP (a filing index only — detail lives in per-filing PDFs),
House/Senate Stock Watcher (**NXDOMAIN, dead**, URLs still in `congress.py:29-30`). **13F is not
a timing instrument** — 45–135 days stale, no intra-quarter round trips, no shorts.

**Legal flag:** the Senate eFD gate quotes **5 U.S.C. app. § 105(c)** — unlawful to use these
reports "for any commercial purpose", up to $10,000 per action, covering House filings too.
Routing through a vendor who has priced that risk in is safer than scraping .gov.

---

## Corrections made against myself

1. **Every figure on the Who to Follow tab was wrong** — measured on 19% of the data. Cisneros
   went from **−5.18% (n=8) to +5.66% (n=107)**, a full sign flip. He had shipped as the page's
   cautionary negative example. *A figure measured on a fraction of the data is not a smaller
   version of the truth; it is a different number.*
2. **The name-merge fix made things worse before better** — canonical names made one member rank
   twice with contradictory returns, which is worse than the caveat it replaced.
3. **An "already moved" metric was built on a field that is always zero.** UW reports
   `price_on_report` and `price_on_filing` identically on every row (278/278 Citadel buys).
   Publishing it would have asserted the disclosure lag costs *nothing*.
4. **Ranking on raw return ranked managers by beta.** All 16 read negative until SPY's own
   −2.44% over the same window was subtracted; Duquesne's "−0.35%" was **+2.1pp of alpha**.
5. **I predicted UW would have fresher short-interest data. It does not** — same settlement date.
6. **A broad `except` around a cache read hid a NameError** in the cache handle. The query still
   ran and the answer still looked right, so the dead cache was invisible.
7. **`git checkout` to undo a sabotage discarded an uncommitted change under test.**

---

## Testing lessons — the expensive ones

**A test that passes under sabotage is a statement about the test, not the code.** This session
had **four separate rounds** where sabotages passed and the fixtures, not the code, were at fault:

| where | why it passed |
|---|---|
| name-merge (3 of 5) | no two given-name-compatible candidates existed; the chamber guard was unreachable; `toks[1] == toks[-1]` for every two-token name |
| alert prefs (4 of 9) | a forged-token test passed *garbage* (fails either way); `ESSENTIAL` unreachable; a sabotage hit a function sharing the same line; timing is unobservable |
| institutional (1 of 7) | the ranking test gave both funds *identical* alpha, so raw and alpha orderings could not differ |
| signal cohort (1 of 8) | asserted the parameter was **bound**; `:dir IS NOT NULL` binds it and filters nothing |

**Three recurring traps, now each documented at their site:**

- **Stale `__pycache__`** after rapid `cp` restores made a sabotage cycle report a false result.
  Every loop now clears bytecode first.
- **SQL-text assertions silently pass against a stubbed sqlalchemy** — `text()` returns a
  MagicMock carrying no SQL. Third occurrence. The working pattern is `inspect.getsource` on the
  one function under test, pinning no numeric literal.
- **A defect the suite structurally cannot catch:** `AlertPreference` was added to `models.py`
  but not exported from `shared/db/__init__.py`, so the endpoint 500'd in production while all
  25 tests stayed green — none import from `db`, because the suite stubs it wholesale. Found by
  curling the live endpoint after deploy.

---

## Verification status

Backend verified end to end: zero drift after every deploy, endpoints exercised against live
production data, auth boundaries checked explicitly (`/api/alerts` 401,
`/api/alerts/unsubscribe/extra` 401, unsubscribe reachable with a valid HMAC and refused
without). One production write was made during verification (unsubscribing user 1 from
`short_squeeze`) and **reverted**.

**Not verified in a browser:** `/reports?tab=funds` and the new alert-preferences section of
`/settings`.

---

## Next

1. **SEC Form 4 insiders** — best evidence-to-effort ratio remaining, and the only source with a
   2-day lag.
2. **The short-interest refresh cadence** — a scheduling fix, not a data fix.
3. **Recipient symbol-matching** — decides who gets mail; needs a deliberate call.
4. **Nothing here changes the 2026-12-04 read** on the signal inversion.
