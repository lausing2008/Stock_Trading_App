# QQQ LEAPS PLAYBOOK — 0.80 DELTA

**Built:** 2026-09-04, from **live market data** (QQQ spot $718.96, real option chains, real
bid/ask, real IV, real open interest). Deltas computed via Black-Scholes from the chain's own
implied vols.

> ⚠️ **IMPORTANT — READ BEFORE ACTING**
>
> This is an **analytical framework**, not financial advice. I am not a licensed advisor.
> LEAPS involve real capital and multi-year risk, and the "right" choice depends on your capital
> base, tax situation, income needs, and risk tolerance — none of which I can assess.
>
> Prices below were live at the moment of writing and **will be stale by the time you read this.**
> Re-pull the chain before any decision.
>
> Also relevant: this platform's own AI BUY signals currently show **negative measured edge**
> (see `SYSTEM_CAPABILITY_ASSESSMENT_2026-09-04.md`). **Do not use platform signals to time this
> trade.** This playbook is deliberately built on *structural* mechanics (delta, decay, leverage),
> not on predictions.

---

# 1. WHICH INSTRUMENT? — QQQ vs TQQQ vs QQQM

**Direct answer: for a 0.80-delta LEAPS strategy, QQQ is the only viable choice of the three** —
but for reasons of *options liquidity*, not because TQQQ "decays away." The measured data below
partially contradicts the usual warning, and I've corrected it accordingly.

## 1.1 MEASURED COMPARISON — all three, same 3-year window

All figures computed from this platform's own ingested daily bars,
**2023-09-06 → 2026-09-04 (~752 trading days)**. Not estimates.

| Metric | **QQQ** | **QQQM** | **TQQQ** |
|---|---|---|---|
| Last close | $718.96 | $295.51 | $72.03 |
| **Total return (3y)** | **+95.2%** | **+95.2%** | **+261.1%** |
| **CAGR** | **25.0%** | **25.0%** | **53.4%** |
| Annualized volatility | 20.5% | 20.3% | **60.8%** |
| Worst single day | −6.21% | −6.11% | **−18.31%** |
| Best single day | +12.00% | +11.74% | +35.24% |
| **Max drawdown** | **−22.77%** | **−22.70%** | **−58.04%** |
| Expense ratio | 0.20% | **0.15%** | 0.88% |
| Options market | **Deepest in world** | Thin / unusable | Liquid but short-dated |
| LEAPS to Dec-2028 | ✅ | ❌ | ⚠️ Available, inadvisable |

## 1.2 CORRECTION: TQQQ did NOT decay away

**I need to correct an overstatement in the first draft of this playbook.** I claimed volatility
decay would severely erode TQQQ. The measured data says otherwise for this period:

| | Value |
|---|---|
| TQQQ actual 3y return | **+261.1%** |
| Naive "3× the QQQ total return" | +285.6% |
| **Actual ÷ naive** | **0.94** |
| Measured avg daily return ratio | **2.96×** |
| Daily return correlation to QQQ | **0.9998** |

**Volatility decay cost roughly 6% over three years — not catastrophic.** The 3× mechanism
tracked almost perfectly (2.96× daily, r=0.9998). The fund did what it says on the tin.

**Even from the worst possible entry:** someone who bought at the Feb-2025 peak and held through
the April crash:

| | Peak→Trough | Peak→Today |
|---|---|---|
| QQQ | −22.7% | **+34.0%** |
| TQQQ | **−56.8%** | **+61.3%** |

TQQQ recovered *and* outperformed even from the worst entry point in the window.

**Why the textbook warning still has teeth, though:** this 3-year window was an almost
uninterrupted bull market (QQQ +95.2%, 25%/yr). Leveraged-ETF decay is a *choppy/sideways market*
phenomenon, and **this dataset contains almost no such period** — consistent with the platform's
own finding that 97.3% of all signal outcomes are `bull` regime. **The data cannot tell you what
TQQQ does in a sideways decade, because it hasn't seen one.** The 2000–2013 Nasdaq period is the
scenario that destroys leveraged ETFs, and it is entirely absent here.

## 1.3 So why is QQQ still the answer for LEAPS?

The case against TQQQ **LEAPS specifically** does not rest on decay. It rests on three things:

**1. Leverage-on-leverage.** A 0.80-delta LEAP already provides ~2.7× leverage. On TQQQ that's
**~8× effective Nasdaq-100 exposure.** The measured −58.04% TQQQ drawdown becomes a near-total
loss on a leveraged claim against it — and unlike shares, **an option can expire worthless
before the recovery arrives.** TQQQ shares recovered from −56.8%; a TQQQ LEAP struck near the
peak very plausibly would not have, because it has an expiry date and shares don't.

**2. Cost of the option.** TQQQ's 60.8% realized vol means far higher IV, therefore far more
extrinsic value to pay for and far more to decay away.

**3. Path dependency multiplies.** Two path-dependent instruments stacked (daily reset × option
expiry) makes outcomes extremely sensitive to *sequence*, not just direction. You can be right
about the Nasdaq's destination and still lose everything on timing.

> **The refined rule:** TQQQ **shares** are a defensible (aggressive, high-conviction) way to
> express a bull view — the data supports that. **TQQQ LEAPS are not**, because the option's
> expiry removes the one thing that saved TQQQ shares: unlimited time to recover.

## 1.4 QQQM — same index, cheaper, but not for options

QQQM tracks the identical index and **performed identically** (+95.2%, 20.3% vol, −22.70% max
DD — matching QQQ to within rounding). At **0.15% vs 0.20%** expense, it is genuinely the
**better share vehicle**, saving ~0.05%/yr.

But its options market is far too thin for LEAPS — wide spreads, negligible open interest.
**You cannot execute this strategy in QQQM.**

> **Practical takeaway:** **QQQM for shares, QQQ for options.** Not competitors — different jobs.
> The measured data confirms you give up nothing on the index side by holding QQQM.

## 1.5 Decision matrix

| Goal | Best instrument | Why |
|---|---|---|
| Cheap long-term core holding | **QQQM** | Identical performance, lowest fee |
| LEAPS / any options strategy | **QQQ** | Only one with real options liquidity |
| Covered-call income overlay | **QQQ** | Needs the liquid chain |
| Aggressive bull expression (shares) | TQQQ ⚠️ | Data supports it — but only with a hard drawdown tolerance and no expiry pressure |
| Aggressive bull via **options** | **QQQ LEAP** | ~2.7× leverage with defined max loss |
| Lower-volatility alternative | SPY | Broader (500 holdings), less tech-concentrated |

## 1.6 If you want TQQQ exposure anyway

The measured data does support TQQQ **shares** as a legitimate aggressive vehicle. If you go
there, the discipline that matters:

- **Shares only. Never LEAPS on it.** Expiry is the thing that turns a survivable drawdown fatal.
- **Size for −60%**, because that is the measured reality (−58.04%), not a worst case.
- **Never on margin.** Margin + 3× = forced liquidation at the bottom.
- **Rebalance on strength** — trim back to target after big runs rather than letting it compound
  into an oversized position.
- **Understand the regime caveat:** its excellent 3-year record was earned in an uninterrupted
  bull market. Treat +53.4% CAGR as regime-dependent, not as a forward expectation.

---

# 2. WHY 0.80 DELTA — THE MECHANICS

At **0.80 delta** you're buying a **stock substitute**, not a lottery ticket:

- **Moves like stock:** +$1 in QQQ ≈ +$0.80 in the option
- **Mostly intrinsic value:** little premium at risk from time decay
- **High probability of finishing ITM:** delta ≈ rough probability of expiring in the money
- **Defined maximum loss:** unlike shares on margin, you cannot lose more than the premium
- **No margin calls, no forced liquidation**

**The tradeoffs, stated honestly:**
- ❌ **No dividends** — QQQ yields ~0.55%/yr, forgone entirely
- ❌ **Time decay is real** — quantified in §3 below
- ❌ **Total loss possible** if QQQ finishes below the strike
- ❌ **Extrinsic value is the price of the leverage** — not free

## Why not other deltas

| Delta | Behaviour | Verdict for this use |
|---|---|---|
| 0.50 (ATM) | ~50% of premium is extrinsic; heavy theta | ❌ Speculation, not substitution |
| 0.70 | More leverage, more decay, lower P(ITM) | ⚠️ Acceptable if you accept more risk |
| **0.80** | **Balanced — leverage + high P(ITM)** | ✅ **The standard choice** |
| 0.90 | Very stock-like, but ties up much more capital | ⚠️ Diminishing benefit |

---

# 3. THE ACTUAL TRADE — REAL NUMBERS (2026-09-04)

## Live chain around 0.80 delta

**QQQ spot: $718.96**

**Jan-2028 expiry (1.38 years out):**

| Strike | Mid | IV | **Delta** | Extrinsic | OI |
|---|---|---|---|---|---|
| $520 | $238.91* | 42.4% | 0.834 | ~$40 | 223 |
| **$550** | **$214.00** | **40.6%** | **0.811** ✅ | **$45.04** | **3,212** |
| $575 | $193.97 | 38.8% | 0.790 | $50.00 | 697 |
| $600 | $174.69 | 37.1% | 0.766 | $55.73 | 5,081 |

\* *Dec-2027 quote shown; Jan-2028 $520 similar*

**→ The 0.80-delta strike is ~$550 for Jan-2028** (delta 0.811, OI 3,212 — good liquidity).

## The economics of that specific contract

```
QQQ Jan-2028 $550 Call @ $214.00 mid

Cost per contract           $21,400
vs. 100 shares              $71,896
Capital freed               $50,496   (70% less capital)

Intrinsic value             $168.96
Extrinsic (time value)      $45.04    (6.3% of spot)
Effective leverage          2.72×
Breakeven at expiry         $764.00   (+6.3% from spot)

Annualized decay cost       $32.64/yr = 4.54% of notional/yr
```

## Read the decay number carefully

**4.54% per year of the notional you control** is the true cost of this leverage. Compare:
- Margin loan: typically 6–13%/yr, *plus* margin-call risk
- **LEAP: ~4.5%/yr, with defined max loss and no margin calls**

That comparison is genuinely favourable — but it means **QQQ must appreciate >4.5%/yr just for
you to match holding shares.** Over 1.38 years, you need **+6.3%** to break even.

**QQQ's actual 3-year return was +93.5%** (~24.6%/yr). If that continued, the LEAP wins
substantially. **It may not continue** — that period included an exceptional tech run.

---

# 4. EXECUTION PLAYBOOK

## Step 1 — Position sizing (do this first)

**Rule: never allocate more to LEAPS premium than you can afford to lose entirely.**

| Portfolio | Suggested max LEAPS premium | Contracts @ $21.4k |
|---|---|---|
| $50,000 | 10–15% = $5–7.5k | ❌ Under 1 contract — use shares/QQQM instead |
| $100,000 | 10–15% = $10–15k | 0–1 (tight) |
| $250,000 | 10–15% = $25–37k | 1 |
| $500,000 | 10–15% = $50–75k | 2–3 |

⚠️ **If one contract exceeds ~15% of your portfolio, this strategy is too large for you.**
Use QQQM shares instead. This is the most common way people get hurt here.

## Step 2 — Expiry selection

**Target 12–24 months to expiry.**
- < 12 months: theta accelerates meaningfully
- 12–24 months: ✅ the sweet spot
- \> 24 months: wider spreads, more capital, less liquidity

**Currently available:** 2027-09-17, **2027-12-17**, **2028-01-21** ✅, 2028-06-16, 2028-12-15

Prefer **January expiries** — they carry the deepest open interest by convention.

## Step 3 — Strike selection

1. Pull the live chain (don't reuse the table above — it's stale)
2. Find the strike with **delta 0.78–0.82**
3. Sanity check: strike ≈ **20–25% below spot** for ~1.4y LEAPS
4. **Require open interest > 500** — $550 (OI 3,212) and $600 (OI 5,081) qualify
5. Check bid/ask spread is < ~3% of mid

## Step 4 — Order execution

- ✅ **Always use limit orders.** Never market-order a LEAP.
- Start at mid, walk up in $0.05–0.10 increments
- Real spread on the $550: bid $211.50 / ask $216.50 → **$5.00 wide (2.3%)**. Paying the ask
  costs ~$250/contract vs mid. Patience pays.
- **Avoid the first/last 30 minutes** — spreads are widest
- Consider legging in across 2–3 tranches rather than all at once

## Step 5 — Ongoing management

| Trigger | Action |
|---|---|
| Delta drifts > 0.90 | Consider rolling up (take profit, reset leverage) |
| Delta drifts < 0.60 | Position has gone against you — reassess thesis, don't average down reflexively |
| **< 9 months to expiry** | **Roll out to a further expiry** — theta accelerates from here |
| Down 40–50% on premium | Pre-committed exit point (decide this **before** entering) |
| Up 80–100% | Consider taking partial profit / rolling up |

**Set these rules before you enter, in writing. Deciding under stress is how positions turn into
disasters.**

---

# 5. INCOME OVERLAY — POOR MAN'S COVERED CALL

**This connects directly to your stated goal of "earn passive income," and it's the part I'd
weight most heavily.**

Once you own the LEAP, sell short-dated OTM calls against it (a diagonal spread, commonly called
a Poor Man's Covered Call):

```
LONG   QQQ Jan-2028 $550 Call   (delta 0.81)  ← your position
SHORT  QQQ ~30-45 DTE OTM Call  (delta 0.20-0.30)  ← income leg, sold repeatedly
```

**Why this fits your goals well:**
- Harvests **volatility risk premium** — a structurally positive, well-documented edge
- **Requires no directional prediction** (unlike this platform's BUY signals, which currently
  measure negative)
- Can meaningfully offset the 4.54%/yr LEAP decay — potentially fully
- Your platform **already has** the covered-call infrastructure (strikes, expiries, premiums, IV
  rank) via the Options Game Plan

**Critical rules:**
1. **Short strike must exceed** `long strike + net debit paid`, or you can lock in a structural loss
2. Sell 30–45 DTE (best theta-to-risk ratio)
3. Target 0.20–0.30 delta on the short leg
4. **Sell when IV rank is high** — your platform tracks `iv_rank_1y` for exactly this
5. Roll the short up/out if QQQ rallies hard through it

**Realistic expectation:** 0.5–1.5%/month on the short leg in normal conditions. That plausibly
covers the LEAP's entire decay cost — which is precisely the point.

---

# 6. RISKS — STATED PLAINLY

| Risk | Reality |
|---|---|
| **Total loss** | If QQQ < $550 at expiry, the contract expires worthless. −22.77% from here ≈ $555. **A repeat of the 2025-04 drawdown would put this position near-zero.** |
| **Leverage cuts both ways** | 2.72× up *and* down |
| **Time decay** | 4.54%/yr of notional, accelerating in the final year |
| **No dividends** | ~0.55%/yr forgone |
| **Concentration** | QQQ ≈ 100 stocks, heavily weighted to correlated mega-cap tech |
| **IV crush** | Current IV ~40% is elevated. If IV falls, your LEAP loses value **even if QQQ is flat.** |
| **Liquidity** | Fine at $550/$600; thin at unusual strikes |

## ⚠️ The IV point deserves emphasis

**Current IV on the $550 Jan-2028 is 40.6% — well above QQQ's 20.4% realized volatility.**

You are buying options priced at roughly **double** the volatility QQQ has actually delivered
over the past three years. Some premium over realized vol is normal (that's the variance risk
premium), but this gap is wide.

**Two implications:**
1. This is a **relatively expensive time to buy** long-dated options
2. It's a **relatively attractive time to sell** short-dated ones → **strengthens the §5 income
   overlay case considerably**

If you do only one thing from this document, consider whether the *selling* side (§5) fits your
goals better than the *buying* side right now.

---

# 7. DECISION FRAMEWORK

**Choose LEAPS if:**
- ✅ You're bullish Nasdaq-100 over 1–2+ years
- ✅ You want capital efficiency (70% less capital for 2.7× exposure)
- ✅ You can lose 100% of the premium without it affecting your life
- ✅ You'll actively manage rolls and the income overlay
- ✅ Position is ≤10–15% of portfolio

**Choose QQQM shares instead if:**
- ✅ You want simple, permanent, low-cost exposure
- ✅ You want the dividend
- ✅ You don't want to manage expiries and rolls
- ✅ One LEAP contract would exceed ~15% of your portfolio

**Choose neither if:**
- ❌ You'd need this money within 2 years
- ❌ A −60% drawdown on the position would force you to sell
- ❌ You can't monitor at least monthly

**On TQQQ:**
- ❌ **Not for LEAPS** — ~8× effective exposure, and the option's expiry removes the unlimited
  recovery time that saved TQQQ *shares* through the measured −58.04% drawdown. See §1.3.
- ⚠️ **Shares are defensible** if you genuinely tolerate −60%, never use margin, and understand
  its +53.4% CAGR was earned in an uninterrupted bull market. See §1.6.

---

# 8. SUGGESTED IMPLEMENTATION

A conservative structure that serves **both** stated goals (growth + passive income):

```
CORE (60-70%)      QQQM shares — cheap (0.15%), permanent, dividend-paying
LEAP (10-15%)      1× QQQ Jan-2028 $550 call (delta ~0.81)
INCOME (ongoing)   Sell 30-45 DTE calls, 0.20-0.30 delta, against the LEAP
CASH (20-25%)      Dry powder for the drawdown that will eventually come
```

**Why this shape:** the core gives you permanent exposure you never have to manage; the LEAP adds
capital-efficient leverage on a bounded, known-maximum loss; the income overlay offsets the
LEAP's decay without requiring any prediction; and cash lets you add on weakness rather than
being forced to sell into it.

## If you want a TQQQ sleeve

The measured data (§1.2) supports TQQQ **shares** more than the conventional warning suggests.
If you want it, the disciplined way to include it:

```
CORE (55-65%)      QQQM shares
LEAP (10-15%)      1× QQQ Jan-2028 $550 call
TQQQ (5-10%)       Shares ONLY — never LEAPS, never on margin
INCOME (ongoing)   Sell 30-45 DTE calls against the LEAP
CASH (20-25%)      Dry powder
```

**Hard rules for the TQQQ sleeve:**
- **Cap it at 5–10%.** At 10%, a repeat of the measured −58% drawdown costs you ~5.8% of total
  portfolio — survivable. At 30% it costs ~17.4% and you will likely capitulate at the bottom.
- **Rebalance back to target after big runs.** TQQQ compounds fast in bull markets; left alone
  it silently becomes an oversized position right before it matters most.
- **Never LEAPS on it.** The whole reason TQQQ shares survived −56.8% is that shares have no
  expiry. Options do.

---

# 9. PRE-EXECUTION CHECKLIST

- [ ] Re-pull the live chain — **the prices above are stale**
- [ ] Confirm delta is 0.78–0.82 at your chosen strike (recompute; don't assume $550 still)
- [ ] Confirm open interest > 500 and spread < 3% of mid
- [ ] Position ≤ 10–15% of portfolio in premium
- [ ] Written exit plan: profit target, stop level, roll date (< 9 months DTE)
- [ ] Understand you can lose 100% of the premium
- [ ] Check current IV rank — is now a *good* time to buy premium?
- [ ] Considered whether §5 (selling premium) fits your goals better than buying it
- [ ] **Not using this platform's BUY signals to time entry** (measured negative edge)

---

# 10. HOW THIS RELATES TO YOUR PLATFORM

**Do use your platform for:**
- ✅ Options Game Plan — real strikes, expiries, premiums, IV rank for the §5 income leg
- ✅ `iv_rank_1y` — timing when to *sell* premium
- ✅ Price/volatility data and drawdown history
- ✅ Alerting on large QQQ moves

**Do not use your platform for:**
- ❌ Timing LEAP entry via AI BUY signals — **measured −2.56% over 12,589 outcomes**
- ❌ Confidence-based sizing — **calibration is currently inverted** (higher confidence →
  worse outcomes)

See `SYSTEM_CAPABILITY_ASSESSMENT_2026-09-04.md` for the full measurement and remediation plan.

**The deliberate design of this playbook:** every element rests on *structural* mechanics —
delta, decay, leverage ratios, volatility risk premium — none of which depend on predicting
direction. That's exactly why it remains usable while the prediction engine is being fixed.
