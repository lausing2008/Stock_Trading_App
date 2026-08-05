# Domain Audit Series — Template & Methodology

Extracted 2026-08-05 from the 6-part sequential platform audit (Tiers 261-266: AI Signal
Performance, Prediction/Decision/Paper-Trading, Model Training/Self-Tuning, Regime/Trend/
Earnings/News/Events, Short Squeeze/Option Expiry, Recommendations/Alerts). Use this as the
starting point any time the user asks for a broad, multi-domain "deep audit" of the platform,
split into sequential per-domain passes with approval gates between each.

This is a DIFFERENT shape from `docs/AUDIT_FINDINGS_TEMPLATE.md` (a lighter checklist for
reviewing a recent code-change batch). Use THIS template when the ask is "audit domain X across
the whole platform and tell me the truth about it" — a standalone investigation grounded in live
production data, not a diff review.

---

## When to use this template

The user asks for something shaped like: *"perform a deep full audit on [N domains], do one at a
time, have my approval for next audit, fix/document them all and update the improvement
tracker."* Key signals: multiple named domains, explicit sequencing ("one at a time"), an
explicit approval gate, and a request to persist findings somewhere durable (the tracker page,
CLAUDE.md, or both).

## Core principles (non-negotiable, learned the hard way across the 6-audit series)

1. **One domain at a time, with an explicit approval checkpoint before the next.** Never batch
   multiple domains into one dispatch even if it seems more token-efficient — the user is paying
   attention to each result and may redirect scope between audits.
2. **Ground yourself in real production data BEFORE dispatching the subagent.** Run your own
   direct queries against the live system first. This does three things: (a) gives the subagent a
   verified factual anchor instead of letting it discover the state cold, (b) lets you catch your
   OWN wrong assumptions before they propagate (this happened multiple times in the series — a
   naive query producing an impossible value, like -566% returns, that turned out to be a percent-
   vs-fraction unit error in the query itself, not the data), and (c) gives you an independent
   basis to verify the subagent's claims afterward.
3. **Give the subagent explicit "already confirmed, do not re-derive" facts** plus explicit
   "already investigated and found NOT a bug, do not re-report" negatives. This prevents wasted
   tool calls re-deriving what you already know and prevents a false-positive from a hypothesis
   that sounds plausible but was already checked.
4. **Independently verify the subagent's 2-3 most consequential claims yourself before writing
   anything down.** Every one of the 6 audits caught something at this step — either a claim that
   needed a small correction, or (in the design-review pass) a claim that was flatly WRONG because
   the subagent had no SSH access and silently read a stale local environment instead of
   production. Never write a finding into a durable record without having traced or queried it
   yourself at least once.
5. **Explicitly record what was checked and found CLEAN, and what was REFUTED.** This is as
   valuable as the findings themselves — it stops a future audit from re-investigating a
   hypothesis that already died, and stops a future session from "fixing" code that already works.
   Every tier in this series ends with one reference entry of this shape.
6. **When a hypothesis is corrected mid-audit, document the correction, not just the final
   answer.** E.g. "an initial hypothesis attributed X to a sample-floor lockout; querying the real
   rows REFUTED that — the actual cause was Y." This is worth recording because it demonstrates
   the verification actually happened, and because the wrong-but-plausible hypothesis will occur
   to someone else again.
7. **Documentation-only unless explicitly asked to fix.** If the user says "document, don't fix,"
   every finding becomes a `todo`-status tracker entry, not a code change. Say so explicitly in
   every commit message.
8. **Never trust "it's healthy" from a shallow check.** A green `docker ps`, a 200 on `/health`, or
   a subagent's "N of M services are running fine" can all be true while the actual thing being
   audited is broken. Verify the SPECIFIC claim, not a proxy for it (see the TLS-cert-expiry
   incident: nginx/frontend/api-gateway were all "healthy" while the public site was completely
   down, because the failure was one layer deeper than any of those health checks look).

---

## Per-domain workflow (repeat once per domain)

### Step 1 — Orient (you, not the subagent)

```bash
wc -l <the 3-6 core files for this domain>
```

Get a feel for scale before touching anything.

### Step 2 — Ground in live data (you, not the subagent)

Run direct queries/greps against the actual running system for this domain. Look specifically
for:
- Aggregate stats that establish ground truth (win rates, row counts, freshness timestamps,
  job-status keys, config values).
- Anything that looks impossible or contradictory on first read — chase it down yourself before
  either dismissing it or handing it to the subagent. (Multiple times in this series, a query
  result that looked like a bug was actually the querier's own unit/sign-convention error.)
- Whether a suspicious-looking gap (e.g. "0 rows ever written") is a live bug or a legitimate,
  rarely-triggered code path — trace the actual condition, don't assume from the absence.

Do NOT hand a hypothesis to the subagent that you could resolve yourself in 2 more queries.

### Step 3 — Dispatch the audit subagent

Use the `Agent` tool (`subagent_type: general-purpose`, `model: opus`, `run_in_background: false`
so you get the result inline before deciding on the next domain). The prompt MUST include:

- **Explicit scope**: "DOCUMENTATION-ONLY audit. Do NOT edit source files."
- **Real production data you already gathered** — paste the actual numbers/tables, not a
  description of them. This is load-bearing: it anchors the subagent and lets you catch drift
  later.
- **A list of "already confirmed by me, do NOT re-derive"** items with the evidence, so the
  subagent doesn't waste its budget re-establishing what you already know.
- **A list of "already investigated and found clean, do NOT re-report"** items, if any exist from
  earlier passes or your own Step 2 work.
- **Context from prior audits in the same series** if findings compound (e.g. "Audit #2 found the
  outcome-writeback is corrupted; check whether THIS domain's tuning consumes that corrupted
  field").
- **A concrete list of lettered audit questions** (A, B, C...) — specific, falsifiable questions
  about the domain, not "find bugs." Each should be answerable by tracing code to certainty.
- **A required output shape per finding**: file:line, one-sentence defect, a CONCRETE failure
  scenario (specific input -> specific wrong output), a severity tier (CRITICAL/HIGH/MEDIUM/LOW),
  and an explicit CONFIRMED-vs-PLAUSIBLE tag.
- **An explicit instruction to list what was checked and found CLEAN** — not just findings.
- **A cap on finding count** (~10-12) ranked most-severe-first, to keep the report actionable.
- **An instruction against false positives**: "trace before reporting."

### Step 4 — Independently verify the subagent's top 2-3 claims

Before writing anything to a durable record, re-run the specific grep/query/code-read yourself
for the most consequential 2-3 findings. This is not optional. In this series it caught:
- A finding whose root cause was correctly identified but whose severity needed re-scoping once
  the real magnitude was measured.
- A finding that was flatly wrong because the subagent's environment (local, not prod) diverged
  from the live system.
- A refutation of the SUBAGENT'S OWN prior-session claim (a design review whose "3-week-stale
  code" headline was itself based on a stale local read).

If a claim doesn't check out, either correct it before recording, or record it as REFUTED with
the evidence — never silently drop a wrong claim without noting why.

### Step 5 — Document (tracker + CLAUDE.md)

**Tracker (`frontend/src/pages/improvements.tsx`):**
1. Allocate the next sequential tier number. Add both a `TIER_LABEL` entry (a full paragraph
   summarizing the headline findings and framing) and a `TIER_COLOR` entry.
2. Add one `ITEMS` entry per finding, using the existing `Item` interface fields (`id`, `tier`,
   `severity`, `title`, `file`, `effort`, `impact`, `what`, `fix`, `defaultStatus`,
   `implementedNote`). Use a consistent `id` prefix per tier (e.g. `AUD262-...`) so a future grep
   can pull the whole audit.
3. Add exactly ONE final `done`-status "AUDIT REFERENCE" entry per tier recording: what was
   checked and found CLEAN, what was REFUTED (with the refuting evidence), and any correction made
   mid-audit. This is the single most reused entry in a future session.
4. **Watch for the TS2590 "union type too complex" compiler error** once the ITEMS array grows
   large across many tiers — it happened in this series at tier 6. Fix: drop the redundant
   `as const` assertions on entries in the newest tier (the `Item[]` array type annotation and the
   `Item` interface's own field types already constrain everything correctly; `as const` is
   redundant and is what pushes the literal-union inference over the compiler's limit).
5. **Verify, don't assume, that new entries reach the shipped page**: run `npx tsc --noEmit`, then
   a full `npx next build`, then grep the COMPILED bundle
   (`.next/static/chunks/pages/improvements-*.js`) for both the new tier label string and a few
   finding IDs. This repo has a documented history of tracker items being added to source but
   never rendering — don't skip this check.

**CLAUDE.md**: append one `## Deep Audit #N of M: <Domain> (YYYY-MM-DD)` section per domain,
containing:
- A one-line scope statement ("documentation-only, Tier N, K entries").
- The production ground-truth table/numbers that grounded the audit.
- The 2-4 headline findings in prose, each with its concrete failure scenario and the evidence
  that confirmed it.
- A "verified CLEAN" paragraph.
- Any REFUTED-claim or self-corrected-framing note.

On the FINAL domain in the series, add a closing "## Audit Series Summary" section identifying
recurring cross-domain themes (in this series: a correct pattern existing in one file and not
propagated to siblings; a "bug-class sweep" declared complete that wasn't; absence-of-data
repeatedly misread as evidence-of-correctness; one root corruption propagating into multiple
unrelated downstream consumers).

### Step 6 — Commit

One commit per domain, `docs: Deep Audit #N/M — <Domain> (Tier NNN)`, body summarizing the
headline findings and explicitly stating "Documentation-only. No source fixes applied, per
standing instruction" if that was the scope. Never bundle multiple domains into one commit.

### Step 7 — Report to the user and get approval before the next domain

Summarize: the 2-3 headline findings with real numbers, anything you refuted or self-corrected,
and one honest sentence about what you did NOT verify. Then ask explicitly whether to proceed to
the next domain — do not assume approval and do not batch.

---

## Adjacent lessons that apply to ANY session touching production during an audit series

- **A subagent without SSH/DB access will confidently report local-environment observations as
  live-system facts if not told otherwise.** Always give it real production data directly in the
  prompt (per Step 3) rather than letting it discover state itself, and always independently
  verify anything it claims about "the system" that it could only have gotten from a local
  checkout.
- **After ANY event that recreates containers (reboot, `docker compose up -d
  --force-recreate`, host maintenance) during or after an audit series, run an EXHAUSTIVE
  file-diff sweep across every service** — not a remembered subset of "the files I touched this
  session." In this series, a reboot recovery that started from a remembered list would have
  missed the majority of the actual drift (52 differing + 12 missing files across 12 services,
  found only once every `.py` under every `services/*/src/` was diffed against the checkout, not
  just the ones a prior session happened to `docker cp`).
- **A live production incident discovered mid-audit (e.g. instance unreachable, TLS cert expired)
  takes priority over continuing the audit series** — diagnose and fix it (with the same
  verify-before-trusting discipline: don't assume `docker ps` health means the actual failure
  surface is healthy), record it as its own incident section in CLAUDE.md, THEN resume the audit
  series where it left off.
- **`docker cp` is a session-scoped hotfix, never a durable deploy.** Every file recovered via
  `docker cp` in an incident-recovery sweep is still owed a real image rebuild; until that
  happens, the NEXT reboot/recreate will silently revert it again. Say so explicitly when
  recording the recovery.

---

## Minimal checklist (copy this per new domain)

- [ ] Oriented on file sizes / entry points
- [ ] Ran my own grounding queries against live production
- [ ] Chased down anything that looked impossible/contradictory myself before dispatching
- [ ] Dispatched ONE subagent, documentation-only, with real data + "already confirmed" +
      "already clean" + lettered questions + required output shape + CLEAN-list requirement
- [ ] Independently re-verified the top 2-3 claims myself
- [ ] Corrected or explicitly REFUTED anything that didn't check out
- [ ] Added tier label + color + N items + 1 CLEAN-reference item to improvements.tsx
- [ ] `tsc --noEmit` clean (watch for TS2590 on a large ITEMS array — drop redundant `as const`)
- [ ] `next build` clean + grepped the COMPILED bundle for the new content
- [ ] Appended a CLAUDE.md section with ground truth + headline findings + CLEAN list
- [ ] One commit, clearly scoped to this domain only
- [ ] Reported headline findings + any self-corrections to the user
- [ ] Got explicit approval before starting the next domain
