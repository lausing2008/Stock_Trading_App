# Using the 3 Squeeze Alerts to Buy the Dip and Sell High

**As of 2026-08-15.** A practical usage guide for the three squeeze-family alerts, aimed at
your stated goal: buying weakness before it recovers, and getting out near strength before it
fades. This describes what's actually running in production today — real thresholds, real
mechanics, no invented behavior.

---

## The core idea: the 3 alerts cover 3 different moments

Think of a short squeeze as having a lifecycle with 3 phases, and each alert watches a
different one:

```
   COILING            MOVE STARTING           MOVE ACCELERATING
(Pre-Breakout Watch) → (Short Squeeze Alert) → (Options Expiry Watch)
   "buy the dip"          "confirmation"          "watch closely / consider selling"
```

- **Pre-Breakout Watch** = the setup is building, nothing has happened yet. This is your
  earliest, highest-risk, highest-reward entry signal — closest to "buying the dip."
- **Short Squeeze Alert** = the move has actually started. Confirmation that the setup is
  playing out, with a real entry/stop/target plan attached.
- **Options Expiry Watch** = large options positioning is about to force dealer hedging near
  expiry — useful both as an early-continuation signal AND as a "this might be getting
  stretched" signal, depending on which side is dominant and how the price has already moved.

You don't have to use all 3 the same way for every trade — but used together, they form a
rough timeline: **watch → confirm → manage the exit.**

---

## 1. Pre-Breakout Watch — your "buy the dip" alert

**What it's telling you:** a heavily-shorted stock (≥15% of float) has gone quiet — its price
volatility (Bollinger Band width) AND its true range (ATR) have both compressed into the bottom
20% of their own 6-month range. In plain terms: the stock has stopped moving much, while a
large short position sits on it. This is the "coiled spring" state — no move has started, but
the ingredients for one are in place.

**How to read the email:**
- **% of float short (as of X, Nd ago)** — always check the age. If it says "45 days ago,"
  treat the number with real skepticism (see §4).
- **Compression read** (BB width / ATR percentile) — lower percentiles = tighter coiling.
- **Volume drying up** — an extra confirming sign, not required.
- **General ML price-direction read** — this is NOT a squeeze-specific prediction. It's the
  app's general model giving its own, unrelated read on price direction. Use it as a *tiebreaker*
  between two similar Pre-Breakout candidates, never as the reason to act.
- **Measured historical win rate (n=X)** — once this shows a real number (it needs 30+ resolved
  outcomes per short-interest band, and the whole alert family is only a couple of days old as
  of this writing, so expect "not enough resolved history yet" for a while) — this is the
  closest thing to a real edge estimate this alert has. Trust the number, watch the `n=` — a
  small n is not a trustworthy signal even when the win rate looks good.
- **⚠ Market regime line** — only appears when the market isn't in a `bull` regime. It never
  means the alert was held back; it's just extra context. In a genuinely weak tape, be more
  conservative about position size, not necessarily about whether to act at all.

**How to act on it for "buying the dip":**
1. Treat this as a *watchlist* signal, not an immediate buy trigger — nothing has moved yet, so
   you're paying no premium for confirmation, but you're also taking on the real risk that
   compression resolves in either direction (a coiled spring can also break DOWN).
2. A good entry approach: wait for the FIRST sign of the coil starting to release in your
   favor — an up day on above-average volume, or the stock reclaiming a short-term moving
   average — rather than buying purely on the compression read alone.
3. Cross-check the compression stock against the Short Squeeze Alert (§2) over the following
   days/weeks — if the SAME symbol later shows up there, that's your confirmation the setup is
   actually resolving upward, and a natural point to add to a position you started small on
   the Pre-Breakout signal.
4. If the `⚠` regime warning is present, size smaller — the stock's own setup can still be
   real, but a weak broader tape makes any individual breakout less reliable.

---

## 2. Short Squeeze Alert — your confirmation + entry-plan alert

**What it's telling you:** a stock with ≥15% short interest just moved ≥3% intraday — a real
move already in progress, not just "green today." This is the loudest, most actionable of the
3 alerts, and the only one with a full entry/stop/target game plan attached.

**How to read the email:**
- **% move today** — the trigger itself; bigger moves = more conviction the squeeze is real,
  but also less "cheap" of an entry.
- **% of float short (as of X, Nd ago)** + **days to cover** — the fuel gauge. A `days to cover
  ≤ 2.0` is flagged 🚨 CRITICAL — this means shorts would need 2 days or fewer of average
  volume just to close their position, a genuinely thin exit that historically correlates with
  the most violent squeezes.
- **Game plan (SWING)** — a real entry/stop/target, computed the same way this app's other
  trading tools do. Use this as your actual risk-management framework, not just a decorative
  number.
- **Measured historical win rate** and **⚠ regime line** — same reading as §1.

**How to act on it:**
1. This is your **confirmation** alert — if you were tracking a symbol from Pre-Breakout Watch,
   this is the "the coil released, it's moving" signal.
2. If this is the FIRST time you're seeing the symbol (no prior Pre-Breakout alert), you're
   entering later in the move — use the game plan's stop distance to size the position
   appropriately; don't chase if the move already looks extended relative to its own stop.
3. The 🚨 critical days-to-cover flag is the closest thing this alert has to "this could
   accelerate hard" — worth tightening your own attention (and maybe trailing your stop up
   faster) on those specific names.
4. This is also a genuinely reasonable place to think about **"sell high"** if you entered on
   the Pre-Breakout signal earlier — a large, sudden % move against a thin short-interest fuel
   supply is exactly the kind of spike that can reverse quickly once the forced buying subsides.
   Consider scaling out some of an existing position into strength here, rather than only ever
   using this alert as a fresh entry trigger.

---

## 3. Options Expiry Watch — your "is this getting stretched" alert

**What it's telling you:** a large, lopsided block of options open interest sits near the
current price, close to expiry (within 5 calendar days). When the market makers who sold those
options have to unwind their hedge near/at expiry, that unwind itself can move the stock. This
is explicitly framed in the app as a **directional watch, not a firm call** — a calls-dominant
signal has historically been associated with EITHER a sharp continuation higher OR a "max pain"
pin/reversal back toward the heaviest strike. The app does not claim to know which.

**How to read the email:**
- **% calls-dominant or puts-dominant** — the side with lopsided open interest. Note the
  thresholds are asymmetric: calls need ≥85% concentration to count as dominant, puts only need
  ≥55% — this reflects a real, structural skew in how equity options normally trade (calls are
  more commonly bought than puts, so a "normal" call-heavy book needs a much higher bar to be
  flagged as genuinely lopsided).
- **Contracts near the money** + **notional** — the "how big is this" context (a $5M+ notional
  floor already filters out thin/illiquid noise before you ever see this alert).
- **Expires in Nd** — pay special attention to `expires TODAY` rows: the open-interest figure
  is still only as fresh as yesterday's close, so treat it as slightly stale on the actual
  expiry day itself.
- **Measured historical win rate** and **⚠ regime line** — same reading as §1/§2, scored
  per-side (calls vs. puts have their own separate, never-pooled win rates).

**How to act on it:**
1. Use a **puts-dominant** reading as a secondary bearish-lean signal — if you're holding a
   position from §1/§2 and this alert fires puts-dominant on the same symbol near expiry,
   that's a reasonable prompt to tighten your stop or take partial profit rather than assume
   the squeeze continues cleanly through expiry.
2. Use a **calls-dominant** reading as a *possible* continuation signal for a position you're
   already in — but because the app is explicit that this can also resolve as a pin/reversal,
   don't treat it as a green light to add size on its own. Cross-check the measured win rate
   for that side first.
3. This alert is the natural place to think about **"sell high"** timing around a known expiry
   date — if you know a large options expiry is coming up on a stock you're long, this alert
   tells you whether the positioning looks likely to accelerate the move (favorable) or looks
   like it's set up for a pin back toward a lower strike (a reason to consider trimming ahead
   of expiry rather than after).

---

## A simple combined workflow

If you want one concrete routine to follow:

1. **Scan Pre-Breakout Watch daily.** Add any genuinely interesting coiling names to a personal
   watchlist (or the app's own "Add to watch" button on the Short Squeeze page — this tracks
   the metric at the moment you add it and emails you once a one-shot revert condition
   confirms the pressure has genuinely faded, so it's a reasonable way to keep an eye on a
   candidate without checking it manually every day).
2. **Wait for Short Squeeze Alert confirmation** on a name you're watching before committing
   real size — this is your actual entry trigger, with a real stop/target attached.
3. **Check Options Expiry Watch** for the same symbol as you approach a known expiry date —
   use it to decide whether to hold through expiry or trim ahead of it.
4. **Always check the short-interest age and the regime line** before trusting any single
   alert's thesis at face value — see §4 below for exactly why.
5. **Watch the measured win rate `n=` count grow over the coming weeks/months.** Right now
   almost every alert will show "not enough resolved history yet" — that's expected, not a
   sign anything is broken (the whole alert family is only ~2 days old as of this writing).
   Once real numbers start showing up, let them meaningfully influence your conviction —
   they're computed from this alert's own actual, resolved forward returns, not a guess.

---

## 4. Two things to always sanity-check before acting on any of the 3 alerts

**Short-interest age.** Real exchange short-interest data settles only ~2×/month, with a 1-2
week reporting lag — meaning a number can legitimately be several weeks stale. All 3 alerts now
show this age (as of 2026-08-15) and reject candidates whose reading is older than 30 days
outright before ever emailing you — but "not rejected" doesn't mean "current." A reading from
25 days ago is still allowed through and could already be stale in practice. If the age looks
old, treat the thesis with real skepticism rather than full confidence.

**Market regime.** All 3 alerts now show a soft `⚠ Market regime: ...` line whenever the
broader market isn't in a `bull` state. This is deliberately NOT a filter — none of the 3
alerts hide anything from you based on regime, since a real squeeze/coiling setup can be a
genuine trade even during a weak tape, and a hard suppression would risk silently hiding the
one alert you'd most want to see. But you should personally use it as a sizing signal: treat a
setup flagged during `risk_off`/`choppy` with smaller size and tighter risk management than the
identical setup during a `bull` regime.

---

## What this guide is not

None of the 3 alerts predict direction with certainty, and none of this app's own copy claims
otherwise — every email states plainly what it does and doesn't know (see
`docs/SHORT_SELL_SIGNAL_ALERT_2026-08-15.md` for the full technical writeup, including the
honest limitation that a real squeeze-breakout-specific trained model doesn't exist yet and
won't for a long time). Use these 3 alerts as structured, real-data-backed context for your own
decisions — not as a system that tells you exactly when to buy and sell.
