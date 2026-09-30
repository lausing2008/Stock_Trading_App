# The dark-pool relative threshold is inert (2026-09-30)

Read-only. SELECTs only; nothing deployed, no threshold changed.

Built to answer the question the reviewer framed, which cuts past the denominator argument
entirely: **how often does the relative threshold actually reject a candidate that already passed
the absolute threshold?**

[Probe](evidence/2026-09-30-darkpool-relative-threshold-probe.py) — reproduces the production
constants and the `_dark_pool_premium_baseline()` definition exactly (trailing 14-day median, only
once a symbol has ≥20 prints in that window).

## The answer: 15 of 9,710. 0.2%.

Both bars apply to the same `premium`, and `AUD-DARKPOOL-NOPERSIST` persists every fetched print
*before* filtering — so this is directly measurable from stored data rather than inferred.

| | |
|---|---|
| Symbols in the 14-day window | 60 (57 with a baseline, 3 not yet qualified) |
| Prints clearing the **absolute** $1M floor | **9,710** |
| …which also cleared the relative bar | 9,692 |
| …**rejected by the relative bar** | **15** |
| Relative rejection rate | **0.2%** |

## Why — and this is the part that matters

The relative floor is `5 × trailing-14d median premium`. For these symbols that median sits around
$130k–$170k, so the relative floor lands around **$650k–$840k** — *below* the $1M absolute floor.

| | Symbols |
|---|---|
| `5 × median` is **below** $1M — relative bar can never bind | **55** |
| `5 × median` is **above** $1M — relative bar can bind | **2** |

For 55 of 57 symbols the relative bar is dominated by the absolute one and is structurally
incapable of rejecting anything. Examples, all with **zero** relative rejections:

```
MRVL  426 prints ≥$1M   baseline $152,886   rel floor $764,430
MU    423                        $129,253             $646,263
TSM   379                        $167,869             $839,343
AAPL  360                        $167,310             $836,550
NET   332                        $157,902             $789,512
DELL  319                        $135,725             $678,626
```

## What this settles, and what it does not

**Settles:** the relative threshold is doing essentially nothing. Whatever selectivity the
dark-pool alert has comes from the **$1M absolute floor**, the **90-minute recency rule**
(`AUD-DARKPOOL-STALEPRINT`) and the alert cooldown — not from the relative bar that
`AUD-DARKPOOL-ABSTHRESHOLD` added.

This also explains, mechanically, why the per-session alerting rate (81–88%) barely moved from the
85–89% that motivated the fix. It is no longer a puzzle about denominators: **the new bar is
inert**, so there was no reason to expect the rate to fall.

The *persistence* half of that work is real and valuable — 375,411 prints where the table
previously held zero, which is what made this measurement possible at all.

**Does not settle:** whether 5× is the wrong multiple, whether $1M is the wrong floor, or what the
alert rate *should* be. The earlier reviews are right that the 5–10% target was never defined
against a stated eligible population, and this measurement does not define one either — it
sidesteps the question rather than answering it.

**Nor does it say the relative bar is a bad idea.** A bar that never binds is not a bar that was
shown not to work; it was never given a value at which it could act.

## If selectivity is the goal

Two levers, and they are not equivalent:

- **Raise the multiple** so the relative floor clears $1M — roughly 6–8× for these medians. Keeps
  the "unusual *for this symbol*" intent, and would start binding on the large-cap names that
  dominate the alert volume.
- **Raise the absolute floor.** Blunter, and re-introduces exactly the failure the relative bar was
  added to fix: a $2M print in AAPL is routine while a $2M print in an illiquid name is not.

Either change should be chosen on held-out outcome and alert-load comparisons, not by picking a
number that produces a target percentage. And per the standing guidance, alert-load is a separate
metric from candidate count: what a recipient receives is not what the scan generated.
