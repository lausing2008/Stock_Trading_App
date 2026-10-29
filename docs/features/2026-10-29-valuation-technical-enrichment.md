# Valuation report — dated RSI and reported EPS growth

October 29, 2026. Adds technical context to the existing Yahoo/UW valuation report using current UW access; no Advanced+ endpoints or new subscription.

The previous implementation correctly refused to derive an adjusted RSI from UW candles whose adjustment basis was unknown. UW also exposes a separately observed RSI in its stock screener. The official schema defines `rsi_14` as 14 regular-session daily close periods and supports an explicit historical `date` and comma-separated ticker list. A live batch for the October 28 report session returned **59 dated RSI observations and 57 reported EPS growth observations** for the 60-name sample.

## Behavior

- One bounded `/api/screener/stocks` request supplies technical observations for the chosen symbols and completed report session. The source's ticker and date must match the request; forming/future sessions and out-of-range RSI values are rejected. RSI zero is valid.
- The existing adjusted-close Wilder calculation remains preferred when its inputs exist. Otherwise a dated UW screener reading is shown with a **UW** label, source/date and an explicit unverified-adjustment note. Different RSI series are not averaged.
- Quarterly EPS growth is the provider's fractional `(current EPS − EPS four reports earlier) / abs(prior EPS)`, displayed as a percentage. It is historical quarterly YoY context and never supplies a forward EPS forecast or PEG denominator.
- CSV includes metric source, date and basis alongside value/state/reason. Old snapshots retain their original gaps and do not acquire current data when opened.
- Successful complete batches cache for 24 hours; partial coverage retries after five minutes. Optional technical-provider/cache errors preserve the fundamentals report. Existing short-interest enrichment remains cache-only.
- Policy changes to `valuation-v2-provider-rsi`. Valuation scenarios, classification thresholds and quality gates are unchanged; RSI and historical EPS growth do not promote a research lead to a qualified opportunity.

## Validation and integration

Tests cover exact-session/ticker filtering, invalid values and zero, cached source timestamps, optional outages, provider versus calculated RSI, rejection of forming readings, conversion of fractional EPS growth, unavailable forward ratios, UI source disclosure and CSV provenance.

The change affects market-data, research-engine and frontend only. The pinned rollout script supports `--data-enrichment` for those three services, with the existing host lock, rollback images and health checks. Production acceptance is captured under [technical enrichment evidence](../audits/evidence/2026-10-29-valuation-rsi/verify_rsi.py).

Source contract: [UW official OpenAPI](https://api.unusualwhales.com/api/openapi), `Stock Screener response` definitions and `/api/screener/stocks` date/ticker parameters. Source data is explicitly provider-reported; price-adjustment comparability and forward estimates are not established by these fields.
