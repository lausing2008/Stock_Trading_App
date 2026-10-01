# Inferred fiscal periods are estimates, and are labelled as facts

**DQ item raised 2026-10-01**, out of the MU earnings review. Not a delivery bug — a data-quality
one, recorded separately because it outlives that incident.

## The mismatch

`EarningsEvent.period`, `.fiscal_year` and `.fiscal_quarter` are **derived from the calendar month
of the period-end date**:

```python
fq = (period_end.month - 1) // 3 + 1
fy = period_end.year
```

That is correct only for a company whose fiscal year is the calendar year. Measured on production:

```
MU  report_date 2026-09-30   stored: period "Q3 2026", fiscal_year 2026, fiscal_quarter 3
    period end 2026-08-31
    actual headline:         "Micron Technology Q4 Adj EPS $33.42 Beats $31.45 Estimate ..."
```

**MU reported Q4. The database says Q3.** Micron's fiscal year ends in August, so the
calendar-derived label is a full quarter out — and this is not an edge case: AAPL (September),
NVDA (January), ORCL (May) and every other non-calendar-fiscal-year company carry the same error.

The code already knows. `_fetch_earnings_for_symbol()`'s own comment calls these "a best-effort
calendar-month label", and `AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH` documents the
limitation. What is missing is any **marking** of the value as an estimate at the point a consumer
reads it.

## Measured blast radius — display only, today

Every consumer was traced before writing this, because "it's just a label" is a claim that needs
checking rather than assuming:

| Use | Found |
|---|---|
| A predicate — `WHERE`, `==`, filter, ordering, grouping | **none** |
| Part of a uniqueness key | **no** — `AUD264` moved that to `(stock_id, report_date)` |
| In an LLM prompt (impact or forecast) | **no** |
| Serialised by the API | **yes** — `/events/earnings` returns `period`, `fiscal_year`, `fiscal_quarter` |
| Rendered by the frontend | **no** — `earnings.tsx` does not reference them, and the TypeScript type omits them |

So today the wrong label is **carried but unconsumed**. That bounds the harm; it does not make it
safe. A field that is wrong for a large minority of symbols, emitted by a public endpoint and
indistinguishable from a verified value, is a trap for the next consumer — and the MU review
nearly walked into it: an early draft of the phase fix proposed matching a headline's `Q4` against
this column, which would have **rejected MU's own result headline**.

## What should change

1. **Label it an estimate at the boundary.** Emit `period_source: "inferred_from_calendar_month"`
   alongside the value, so a consumer cannot mistake it for authoritative.
2. **Store authoritative fiscal periods separately** when a source provides them — the issuer's own
   release, the 8-K, or a provider field — rather than overwriting the inferred value with
   another inferred value.
3. **Never use the inferred value as a key or a predicate.** A test now asserts the market-data
   scheduler does not reference `fiscal_quarter` at all; the same rule should hold wherever a new
   consumer appears.

## Explicitly not done

No schema change, no backfill, no correction of existing rows. Correcting the label needs
authoritative fiscal-calendar data this platform does not currently hold, and inventing a second
inference would repeat the mistake with more confidence. Recording the defect and bounding its
reach is the whole of this item.
