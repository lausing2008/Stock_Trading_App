# Claude Usage Audit — Token Waste Report

**Date:** 2026-09-01 (revised 2026-09-01 after review — see "Corrections" below)
**Scope:** This project's Claude Code session (`cd822f72-...`), CLAUDE.md, connected
tools/MCP, model config, hooks, subagents, scheduled work.
**Method:** Direct measurement — `wc`/`grep` against real files, `tiktoken` (cl100k_base,
a proxy for Claude's own tokenizer) against CLAUDE.md, and a direct parse of the session's
own JSONL transcript for real `usage` fields. No file or setting was changed to produce
this report.

---

## Corrections Made During Review

The first draft of this report contained two material errors, both found by re-measuring
rather than re-reading the draft. They are corrected throughout, and recorded here because
the *reasoning* errors matter more than the numbers:

1. **"CLAUDE.md is the dominant driver of the 917k floor" — OVERSTATED.** CLAUDE.md is 59%
   of the *minimum* per-turn floor, but only ~46% at the median and ~37% at peak. The
   other ~54% is transcript + tool-result growth. Tool results alone total ~370k tokens —
   *more* than CLAUDE.md. The original draft asserted dominance from the single largest
   number without checking its share of the total.
2. **The real cost driver was missed entirely: 21 full cache rebuilds.** The original draft
   flagged cache behavior as GREEN ("working as intended") based on the 99.1% cache-read
   ratio. That ratio is real, but it hid the actual expense: 14.0M tokens of
   *cache-creation* across 21 rebuild events, billed at a premium over base input. This is
   the single largest controllable cost in the session and did not appear in the first
   draft at all.

---

## Findings

Sorted by cost, highest first.

| FINDING | SEVERITY | EVIDENCE | WHAT IT IS COSTING ME |
|---|---|---|---|
| **21 full cache rebuilds × a 347k-token CLAUDE.md = ~7.3M premium-billed cache-creation tokens** | 🔴 RED | Session log: 21 turns with `cache_creation_input_tokens` > 50k (551k–871k each), totalling **14,020,871** tokens — 85% of the session's entire 16,478,182 cache-creation total. CLAUDE.md (347,000 tokens) is re-written on every one of them. | The dominant *billed* cost of the session. Cache writes cost more per token than base input; cache reads cost far less. Every rebuild re-pays for CLAUDE.md in full. |
| **Only 4 of 21 rebuilds are idle-timeout expiries — 6 fire inside the cache TTL** | 🔴 RED | Gaps between distinct rebuild events: 98.2, 188.7, **10.1**, 44.8, **11.2**, 404.6, **21.2**, 606.1, **52.0**, **12.9** min. Four exceed a 60-min TTL (genuine expiry); six do not. | The 6 sub-TTL rebuilds are *avoidable* — they are triggered by compaction/context changes, not idle time. Each one re-pays CLAUDE.md's 347k tokens for no benefit. |
| **CLAUDE.md is 347,000 tokens, loaded in full on every turn** | 🔴 RED | `.claude/CLAUDE.md` — 1,404,650 bytes, 20,077 lines, 252 `##` sections. Independently re-verified: 346,999 tokens (cl100k_base), 4.02 chars/token. **No `@imports`** (count: 0), so this is the complete figure, not an undercount. | Sets the floor for every turn AND is the multiplier on every rebuild above. 59% of the minimum floor (586,739), 45.8% of the median (757,612), 37.2% of the peak (934,050). |
| **Tool results total ~370k tokens — more than CLAUDE.md — and are NOT concentrated in a few big blocks** | 🟡 AMBER | 1,241 `tool_result` blocks, 1,482,237 chars (~370,559 tokens). Only **24 blocks exceed 8,000 chars** (~69,509 tokens total, 19% of tool-result volume). The other ~81% is spread across ~1,200 small-to-medium results. | The second-largest context consumer. **Important:** because the bulk is many small results rather than a few giant ones, output-truncating hooks would recover comparatively little — this corrects the first draft's implied fix. |
| Total memory exceeds a reasonable budget by ~35x | 🔴 RED | 347,000 tokens vs. a 10k-token reference budget; single-file guidance (5k) exceeded by ~69x | Restated against a normal budget — not a borderline case in either direction. |
| Cache-read is 99.1% of tokens moved — but this is a *ratio*, not a cost verdict | 🟢 GREEN | cache_read=1,694,764,523 / cache_creation=16,478,182 / input=4,358 / output=1,041,014 across 2,235 assistant turns | Caching itself is functioning correctly. Flagged GREEN for *mechanism*, but see the top two RED rows: a high read-ratio does not mean low cost when the write side is 16.5M tokens. |
| Discrepancy: hook-duration metrics non-zero, but 0 hooks configured anywhere | 🟡 AMBER | `~/.claude.json` cached `lastSessionMetrics`: `pre_tool_hook_duration_ms_count: 86`, `hook_duration_ms_count: 58` — vs. zero `hooks` keys in project `settings.json`, `settings.local.json`, user `~/.claude/settings.json` | UNKNOWN whether these reflect real hooks not found in those 3 files, or internal instrumentation. Unresolved — reported rather than guessed. |
| No output-filtering PreToolUse hooks exist | 🟡 AMBER | Zero `hooks` entries in all 3 checked settings files | Real, but **lower-value than the first draft implied** — see the tool-result row above. Worth doing only for specifically identified noisy commands, not as a general fix. |
| MEMORY.md is negligible | 🟢 GREEN | 3,428 bytes (~850 tokens) | Not a contributor. Ruled out. |
| No MCP servers connected | 🟢 GREEN | `.mcp.json` not found; project `mcpServers`: `{}`; enabled/disabled lists both empty | Zero tool-schema overhead. Ruled out. |
| Tool deferral ACTIVE; no proxy/gateway defeating it | 🟢 GREEN | 17 tools listed as deferred at session start, requiring `ToolSearch`. No `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`/gateway var found | Deferred schemas correctly excluded. Ruled out. |
| No model switch within this session | 🟢 GREEN | Single model string `claude-sonnet-5` across all assistant turns in this log | No mid-session cache rebuild from a model change. |
| Zero custom subagent files | 🟢 GREEN | No `agents` dirs under project `.claude/` or `~/.claude/` | Nothing to audit. |
| Zero scheduled/cron/background jobs | 🟢 GREEN | No crontab entries, no Claude-related systemd timers | No interval-vs-TTL comparison needed. |
| Effort level: UNKNOWN | — | Not present in the session log; not queryable outside a live session | Cannot assess. Check `/config` interactively. |

**Single highest-leverage change: shrink CLAUDE.md from 347k tokens to a small always-loaded
core, because it is both the per-turn floor AND the per-rebuild multiplier — it is the only
item that appears in all three of the RED findings above.**

---

## Why This Is the Right Fix

CLAUDE.md today is one continuously-growing changelog — every bug fix, feature build, and
incident postmortem gets appended as its own dated `##` section, forever. The content is
genuinely valuable (this project's history shows it repeatedly prevents re-investigating
already-fixed bugs and catches stale tracker claims). But "valuable to have on record" and
"must be re-read in full on every turn, and re-paid for on every cache rebuild" are
different requirements, and this file currently conflates them.

The corrected cost model makes the case stronger than the original draft did, not weaker:

- **Per-turn:** 347k tokens of floor, crowding out working context and pulling compaction
  forward. (Earlier compaction → more rebuilds → see below. These compound.)
- **Per-rebuild:** 347k tokens re-billed at cache-creation rates, 21 times this session.
  That is ~7.3M premium tokens for content that did not change once.

Cutting the core to ~5k tokens addresses both simultaneously. It also reduces rebuild
*frequency*, because a smaller floor means more room before compaction triggers.

## Recommended Solution — Split by Access Pattern

**1. Keep a small always-loaded core in `.claude/CLAUDE.md`** — target under 5k tokens:
   - Deployment pattern (how to build/deploy each service).
   - Security constraints (SSH key paths, never-commit rules).
   - A short list of currently-active, most-likely-to-recur gotchas — as **one-line
     pointers**, not full incident writeups.
   - An index of where the rest lives (see #2).

**2. Move the 252 dated sections into `docs/`, grouped by topic — not by date.**
   `docs/` already holds 69 files and already uses this pattern for design/audit docs:
   - `docs/incidents/<bug-class>.md` — one file per *recurring bug class*
     (`redis-connection-pooling.md`, `shared-db-staleness.md`,
     `delisted-stock-generation-blind.md`), each holding every dated entry for that class.
     Grouping by class is the point: a session asking "is this the same bug as last time"
     reads one topic file, not 20,000 lines.
   - `docs/features/<area>.md` — per shipped feature area, with its build history and
     "what to check if this looks wrong" sections.
   - `docs/audits/` — consolidate the standalone audit-review entries.

**3. Load on demand.** Reference split-out files from the core's index and read them only
   when a task touches that area — never as part of the unconditional floor.

**4. Write new entries into the topic file, not back into CLAUDE.md.** Without this
   discipline the file regrows to its current size within months.

**Expected effect:** core drops to ~5k tokens.
- Per-turn floor: ~347k → ~5k (**~98% cut** to this component).
- Per-rebuild cost: ~7.3M → ~105k tokens across the same 21 rebuilds.
- Plus a second-order gain: a smaller floor delays compaction, which should reduce the 6
  sub-TTL rebuilds too.
- **Zero loss of history** — it is all still on disk, read when relevant.

## Secondary Items, Correctly Prioritized

- **Reduce avoidable rebuilds** (RED, but partly outside file-level control). Four of 21
  were genuine idle expiries; batching work into contiguous sessions rather than returning
  after multi-hour gaps avoids those. The 6 sub-TTL rebuilds should shrink on their own
  once the floor is smaller.
- **Targeted output filtering, not blanket hooks** (AMBER, downgraded from the first
  draft). Since only 24 of 1,241 tool results exceed 8k chars, a general truncation hook
  is low-yield. If pursued, target the specific commands that produced the largest results
  (24k, 20k, 17k, 16k chars) rather than adding broad filtering.
- **Resolve the hook-metrics discrepancy** — determine whether those counters reflect real
  hooks not found in the 3 checked settings files, or internal instrumentation.
- **Check the live effort level** via `/config` — not observable from outside a session.

## What Was Deliberately NOT Changed

Per the original audit's constraint, no file content, setting, hook, or MCP config was
modified — this report and the tracker entries are the only additions. The CLAUDE.md split
is a recommendation to execute as a follow-up, not something performed here.
