# Email alert audit remediation — EA-01…EA-12, and a four-day outage I caused

Remediates [`2026-09-28-email-alert-system-audit.md`](2026-09-28-email-alert-system-audit.md).
Evidence: [`evidence/2026-09-28-email-alert-evidence.json`](evidence/2026-09-28-email-alert-evidence.json),
[probes](evidence/2026-09-28-email-alert-reproductions.py). Fixed in `c2703484`, deployed at
`c2703484` with drift 0/12.

## EA-01 was a live outage, and it was mine

`check_signal_alerts` evaluated this inline, as an argument:

```python
cohort_stats=_signal_cohort_stats(session, new_signal, style)
```

`new_signal` is the **keyword name** of another argument in the same call, not a local
variable. Every evaluation raised `NameError` before `send_signal_alert_email` was entered. The
surrounding `try` counted that as a delivery failure, and after five the give-up branch
advanced `last_signal` — consuming transitions nobody was ever told about.

| | |
|---|---|
| Introduced by | `f7e9fea3`, 2026-09-23 |
| Last signal email in production | **2026-09-24 03:40:45** |
| Subscriptions | **362** |
| Sent in the 7 days to 2026-09-28 | 14 |

So: roughly four days of complete silence on the platform's headline feature, with alerts
permanently discarded along the way, and **every test in the suite stayed green** because
nothing ever executed that call.

Three things were wrong, not one:

1. The name. Now `current`.
2. The coupling. A win-rate **badge** was evaluated inline with the send, so any error in it
   took the alert down too. It is computed separately, in its own `try`, and a failure sends
   the alert without the badge.
3. The consumption. DP-1's five-failure give-up exists for a broken SMTP config that will
   never recover on its own. A `NameError` is the opposite — it succeeds the moment the code is
   fixed — and advancing `last_signal` threw the notification away permanently. A raised
   exception no longer reaches that branch.

## The rest

| | Finding | Fix |
|---|---|---|
| **EA-02** | `min(1.03, tp_mult * 0.8)` is **0.896** for SWING, so an analyst target only had to clear 89.6% *of the current price*. Target 90 at price 100 rendered entries 98.5/96.5, stop 94.5, take-profit **90** — a bullish plan whose profit target sat below its own stop | `max`, plus `_plan_geometry_ok` validating the **output** so every route is covered rather than each input in turn |
| **EA-03** | `days_to_earnings or 99` turned "earnings **today**" into a "clean runway"; the risk line's truthiness test skipped zero too. With no evidence at all the helper asserted analyst agreement, improving structure and supporting volume | Four states told apart (unknown / today / upcoming / overdue); absent evidence says so |
| **EA-04** | The price template read `condition == "above"` and called everything else "fallen below", so a bullish MACD cross rendered as *"has fallen below 0.0"* — wrong direction, mismatched units, MACD dropped from the body, and a footer promising it would never fire again | Crossings keep their exact wording; indicator events render as themselves and honour `recurring` |
| **EA-05** | **Twelve of 23** advertised preference types had no enforcement anywhere — the settings toggle wrote a row nothing read. The flow digest had no registry entry at all and mailed inactive accounts | All twelve wired, `flow_digest` registered and filtered, inactive accounts excluded, **and a ratchet test** that fails when a 24th type is added without enforcement |
| **EA-06** | A failed price-alert email left nothing retryable; the next scan filtered the alert out | `triggered` stays committed (the market event happened); `last_sent_at` carries the notification's own retryable state, bounded to 24h |
| **EA-07** | The top-3 composition advanced **before** delivery, so a total failure never retried anyone | Advances only when no recipient is still owed it; per-recipient dedup prevents duplicates |
| **EA-08** | Unverified freshness and an unreachable decision-engine both failed open into new actionable BUYs | Exits still send; BUYs **defer** without consuming the transition |
| **EA-09** | `ask >= bid` classified a dead tie as "aggressive BUYING"; a missing side became zero | A side must dominate ≥60% of classified premium; the template reports the measured imbalance instead of asserting intent |
| **EA-12** | SMTP had no timeout — a stall can outlive the 30-minute lock lease around it. Paper exits logged `exit_email_sent` regardless of the sender's boolean | 30s transport timeout; the log now reflects the result |

## Why the enforcement ratchet, rather than twelve more calls

`_filter_by_alert_pref` already existed and worked. It was simply never called from those
twelve jobs — which is the failure mode of a per-job check: every new job has to remember.
Adding twelve calls fixes today and guarantees a thirteenth. So the durable part of EA-05 is
`test_every_manageable_alert_type_is_enforced`, which reads the real registry and fails the
build for any non-essential type whose delivery path consults nothing.

## Verification

30 new tests, 8 sabotages, all confirmed red then green.

Two process notes worth keeping:

* **A test had pinned the decision-engine fail-open as a requirement**
  (`test_fail_open_on_de_unreachable_is_unchanged`), so correcting a safety veto that fails
  open read as a regression. Reversed with its original reasoning quoted rather than deleted —
  the fourth time this has been necessary in this project.
* **The first draft pushed the AUD-T401 ratchet 181 → 191**, by asserting thresholds as source
  substrings (`>= 5`, `1.03`, `* 0.8`, `<= 10`, `hours=24`). Rewritten behaviourally against
  the real builder and via the AST; back to 181.

And a seventh prose collision: my own verification command reported "NameError gone: False"
because the explanatory comment beside the fix **quotes the defective line**. The code was
correct; the check was reading prose. Strip comments before asserting on source — this is now
a reliable enough pattern to expect rather than rediscover.

## Not addressed here

EA-10 and EA-11 are measurement-design findings — immutable event IDs with separate watch and
actionable TTLs, and separating candidate diagnostics from delivered-alert outcomes from
executed paper trades. Both need schema and a forward trial, not a patch, and the audit
presents them that way. EA-12's transactional outbox and lease fencing are likewise scoped
beyond a timeout: what shipped is the bounded transport and the honest log.
