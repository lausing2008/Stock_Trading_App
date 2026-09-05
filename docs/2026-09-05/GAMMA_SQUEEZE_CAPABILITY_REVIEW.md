# CAN THE SYSTEM CATCH A GAMMA SQUEEZE BEFORE THE RISE?

**Date:** 2026-09-05
**Question:** the system is meant to catch options that can't be "recovered" — dealers forced
to buy back stock, pushing price up — and alert *before* the rise. Is that a valid statement
about what's built, and is it achievable?

---

## Short answer

**Partly valid, but overstated as described — and not achieved today.**

- The mechanism you described (a **gamma squeeze**) is real and is genuinely what
  `check_gamma_unwind_alerts()` is aiming at. That part is a fair statement of intent.
- **But the alert does not detect forced buy-back.** It detects *open-interest concentration*
  near the current price close to expiry — a proxy for "hedging risk is elevated here", which
  is **not** the same as "dealers are short gamma and will be forced to buy."
- The alert is explicitly a **directional watch, not a BUY call**, by its own design.
- Measured performance is poor: `gamma_unwind_calls` **24.5% win rate** over 64 resolved
  outcomes. It is not currently predicting the rise.
- It **is** achievable to get materially closer, because the missing ingredient (real dealer
  gamma positioning) is **already available and already being fetched** — it is just not used
  to make the decision.

---

## 1. What the mechanism actually is

A gamma squeeze:

1. Traders buy large amounts of calls near the money.
2. The dealers who sold those calls are now **short gamma** — to stay delta-neutral they must
   buy the underlying stock as price rises.
3. That buying pushes price up, which forces more buying, which pushes price up further.
4. Near expiry the effect intensifies, because gamma is highest close to expiry at the money.

The essential precondition is **direction of dealer positioning**: dealers must be *short*
gamma. If dealers are *long* gamma, the identical open interest produces the exact opposite
behavior — they sell into strength and **dampen** moves rather than amplify them.

That distinction is the whole ballgame, and it is precisely what the current alert cannot see.

## 2. What the system actually detects

`check_gamma_unwind_alerts()` (scheduler.py:4137) measures, per symbol:

- Total call and put open interest within ±X% of the current price
- Whether that OI is lopsided toward calls or puts (`dominant_side`)
- Total notional (`OI × 100 × price`) above a floor
- Proximity to expiry

Its own docstring is admirably honest about the limit:

> **HONEST LIMITATION, stated explicitly rather than papered over: this is NOT a real
> gamma-exposure (GEX) calculation.** A true GEX model needs each contract's actual gamma …
> and a maker-positioning assumption (are dealers net long or short gamma at each strike) —
> neither is computed anywhere in this app. What IS built here is a defensible PROXY.

So: **the code never claims to detect forced buy-back, and it doesn't.** It flags "a lot of
options are stacked near this price close to expiry." Whether that produces a squeeze, a
dampening, or nothing depends on dealer positioning the proxy cannot observe.

**Important naming caveat:** `gamma_unwind_calls` means *call-side OI dominates*. It does
**not** mean "bullish" or "a squeeze is coming."

## 3. Does it work? — measured

`squeeze_alert_outcomes`, forward returns to 5 days:

| Alert type | Fired | Resolved | Avg 5d return | Win rate |
|---|---|---|---|---|
| `gamma_unwind_puts` | 211 | 119 | −0.005% | **31.8%** |
| `gamma_unwind_calls` | 98 | 64 | −0.011% | **24.5%** |
| `short_squeeze` | 11 | 11 | −0.062% | 9.1% (n too small) |

**No, it is not currently catching the rise.** A 24.5% win rate on the call-dominant bucket is
worse than a coin flip, on a real sample (64 resolved). This is the honest state of it.

To be fair to the design: the alert never promised to predict direction — it is framed as a
watch. But as an answer to "does it tell me before it rises?", the data says no.

## 4. Why — and what would fix it

### The missing ingredient already exists

`get_gex_levels()` (unusual_whales.py:432) returns **real, calculated dealer gamma**:

- `call_wall` — strike where dealer call gamma concentrates
- `put_wall` — the put-side equivalent
- **`gamma_flip`** — the "zero gamma" price where dealer hedging **flips direction**

`gamma_flip` is the exact variable the proxy is missing. **Above it, dealers are typically
short gamma and amplify moves (squeeze conditions). Below it, they are long gamma and dampen
moves.** Same OI, opposite consequence.

### But it is only used as decoration

In `check_gamma_unwind_alerts()`, real GEX is fetched **after** candidates are already chosen
(`AUD-GEXCORROBORATE`, scheduler.py:4309) and only attaches a corroboration line:

> a real GEX reading sitting close to the current price is genuine, independent evidence …
> **not a replacement of the proxy (which still gates/drives every candidate above regardless)**

So the system fetches the number that would tell it whether a squeeze is even mechanically
possible — and then does not use it to decide.

### Second gap: corroboration is not measured

`squeeze_alert_outcomes` has **no column** recording whether an alert was GEX-corroborated. So
the obvious question — *"do GEX-corroborated alerts outperform?"* — **cannot be answered from
stored data**. Same class of measurement gap as the dark-pool print table (fixed today).

## 5. Is it achievable?

**Yes, meaningfully closer — with honest limits.**

### Achievable

1. **Gate on `gamma_flip`, don't just display it.** Only fire a call-side squeeze alert when
   price is *above* gamma_flip (dealers short gamma → amplification). This is the single
   highest-value change and the data is already being fetched.
2. **Use `call_wall` as the target, not just a nearby level.** A squeeze typically runs *toward*
   a call wall and stalls there — that gives a price objective, not just a warning.
3. **Record GEX corroboration in outcomes** so the hypothesis becomes testable at all.
4. **Require confirming flow.** A gamma squeeze needs someone actually buying the calls —
   `check_options_flow_alerts()` already detects aggressive ask-side call sweeps. Squeeze
   conditions *plus* live aggressive call buying is a far stronger joint signal than either.

### Not achievable

- **Reliably alerting *before* the rise.** By the time OI concentrates, price is near the wall,
  and flow turns aggressive, the move is usually underway. This is the same **structural
  late-entry problem** documented in `WHY_SIGNALS_FIRE_LATE.md`: every confirming indicator is
  a momentum measure, so waiting for confirmation guarantees lateness.
- **Certainty about dealer positioning.** Even real GEX infers dealer books from public data;
  it is a model, not a ledger.
- **Predicting the squeeze itself.** Most OI concentrations resolve into nothing. Even a
  perfect model raises the odds; it does not make it a signal you can size aggressively on.

## 6. Honest bottom line

> *"The system catches options that can't be recovered, forcing buy-backs, and alerts me before
> the rise."*

- **Intent:** valid — that is the phenomenon being targeted.
- **Implementation:** it detects OI *concentration*, not forced buy-back, and its own docstring
  says so.
- **Result today:** 24.5% win rate on call-dominant alerts. It is not delivering that promise.
- **Potential:** real, because `gamma_flip` is already available and unused for decisions.
  Gating on it would let the alert distinguish "dealers must buy into strength" from "dealers
  will sell into strength" — the difference between a squeeze setup and its opposite.
- **Ceiling:** even done well, this is a *probability-raiser and a risk warning*, not a
  before-the-move predictor. Treat it as "conditions favor an amplified move here", never as
  "this will go up."

## Recommended next step

Before building anything: **add the GEX-corroboration flag to `squeeze_alert_outcomes` and let
it accumulate for a few weeks.** If corroborated alerts do not outperform uncorroborated ones,
the gamma_flip gate will not rescue the alert either, and that is worth knowing before investing
in it. This is cheap, and it converts an argument into a measurement.
