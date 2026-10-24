# Options horizon fit and context evidence — October 23, 2026

Implementation validated locally; production rollout/verification is recorded below after deployment. This is a frontend-only follow-up to explicit covered-call alternatives. No signal thresholds, model weights, provider ingestion, account orders or backend code changed.

## Delivered behavior

The Options tab now exposes the existing SHORT, SWING, LONG and GROWTH horizon selector. The context passed to the strategy panel is the exact selected symbol/horizon signal; there is no fallback to another style. Switching styles reuses already loaded signals and the options plan; it adds no options-chain/provider call. Existing page-level signal-history/outcome subscriptions still follow the selected style as before.

Each strategy's conditional goal fit now checks a declared **1–365 calendar-day** holding period. The expiry must cover this period **plus two calendar days**, measured from the current New York calendar date, rather than trusting a cached `days_to_expiry`. The buffer is an explicit research policy, not an optimized exit rule. Same-day/intraday options support is not introduced. A 30-second UI clock updates date/expiry and record-age checks while the tab remains open, without network polling.

Editable defaults mirror the current `_OUTCOME_HOLD_DAYS` in `services/signal-engine/src/api/signals_shared.py`:

| Selected horizon | Initial planning days |
|---|---:|
| SHORT | 7 |
| SWING | 14 |
| LONG | 28 |
| GROWTH | 14 |

These are existing evaluation windows, **not recommendations for how long to hold** and not inferred durations of analyst price targets. The user can change them. An override is bound to symbol/horizon and does not leak to another horizon. These frontend defaults are explicit versioned policy; changes to the backend outcome windows require a deliberate review/update, not silent synchronization. Strategies that expire too soon remain visible with their specific blocker. Multi-expiry candidate search remains future work.

## Recorded evidence and limits

The panel separates the selected AI signal, recorded market regime, recorded daily SMA50/200 trend, an analyst-recommendation revision proxy, and unavailable option-horizon return forecast evidence. Values from a wrong symbol/horizon are hidden. Missing values remain unavailable; genuine false/unchanged diagnostics are preserved. Trend booleans are shown literally as recorded yes/no flags; a false above-average flag is not called a below-average observation, because equality or unavailable calculations can also produce false.

An explicitly zoned signal timestamp can establish the record's age against a disclosed **72-hour research freshness limit**. Missing/invalid timestamps, unverified timezones, future timestamps, stale records and mismatched symbols/styles have separate states. A timezone-less DB timestamp is not interpreted in the viewer's local timezone. The signal row's timestamp is never presented as the observation date of its underlying market, technical or company inputs. Those source dates are currently absent and remain unverified. A fresh saved signal also does not prove its inputs were fresh. These recorded fields share one signal record; they are not independent votes.

Code inspection found that `eps_revision_direction` actually compares `recommendation_mean` across up to eight weekly `fundamentals_snapshot` rows (`generators/signals.py`, T220-F). The UI labels it **analyst recommendation revision**, with snapshot dates unavailable. It does not call it an EPS revision or company forecast. The stored feature and its existing scorer are unchanged; correcting their broader naming/provenance is separate work.

The panel can flag conflicting recorded signal/regime values, but does not score them or infer a winner. It does not treat model confidence or bullish probability as option profit probability, and does not convert a long-term analyst target into an option-expiry forecast.

**Market-aware preference remains unavailable.** Where existing objective/holdings/budget/quote/expiry checks find a fit, its banner now says **“Goal-fit only”**. The original comparison-only/no-fit WAIT state and entry WAIT for fresh quotes/broker checks remain. Missing context is not a fabricated neutral rating. This completes horizon/expiry safeguards and evidence readiness, not the complete regime/forecast strategy ranking.

## Next data work before market-aware ranking

1. Store technical input bar timestamps, canonical regime observation timestamps and fallback status separately from signal record time.
2. Expose the actual recommendation snapshot dates/window and model/source versions; distinguish recommendation changes from EPS revisions.
3. Establish dated earnings/ex-dividend coverage and calibrated forecasts that match the declared holding/option horizon.
4. Compare candidates across eligible expirations with execution costs, assignment, dividends and downside stress.
5. Validate immutable all-candidate outcomes on chronological held-out/forward data before introducing expected-return ranking or automatic settings promotion.

## Validation

**864 frontend tests passed across 72 files**, full typecheck and whitespace checks passed. Focused tests cover four-horizon binding, wrong-symbol refusal, invalid/future/stale/timezone-less timestamps, real false/zero observations, proxy labelling, expiry/holding boundaries for all four strategies, a changed horizon without options refetch, override isolation and aging in an open tab. No backend tests were rerun because backend code was unchanged. No interactive browser acceptance is claimed. [Validation evidence](evidence/2026-10-23-options-horizon-context/).
