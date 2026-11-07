# Whole-platform review: toward an institutional-quality investment workstation

November 6, 2026. **Source review and design only. No application/configuration changes.** This extends the [trading-feature audit](audit.md); its 18 findings remain applicable. Follow-on specifications: [consolidation](consolidation.md), [user experience](experience-design.md), and [institutional controls and roadmap](institutional-roadmap.md).

## Executive judgment

The platform is a capable research and trading-workflow system with unusually broad coverage for a small application. Its strongest assets are the existing data integrations, evidence-aware research, configurable stock workspace, deterministic strategy components, explicit unknown states, and accumulated regression/concurrency tests.

Its main weakness is **inconsistent maturity across a fragmented product**. A sophisticated chart or report may sit next to a heuristic score, an experimental model, a historical performance view with a different population, or a broker path whose safer lifecycle is behind a rollout flag. Users must assemble the meaning across too many places.

The right ambition is an **institutional-quality research, portfolio and execution workstation**, scoped initially to the current US/HK equities and supported US options capabilities. Institutional quality means reproducible decisions, reconciled accounts, explicit risk, reliable operations and usable workflows. It does not mean every institutional asset class, expensive infrastructure, guaranteed alpha, or certification as a regulated institution.

Build the product around a durable chain:

**Evidence → investment thesis → setup → approved plan → order intent → broker execution → position/risk → attribution and learning.**

Each transition should have an owner, identity, timestamp, permissions and failure state. Different investment styles share that chain without sharing inappropriate forecasts or execution assumptions.

## Review basis and limits

The local inventory found **76 page components**, **12 backend service entrypoints**, and **115 table declarations in the principal ORM file**. Page count excludes Next application/document wrappers and API handlers; it includes login, guide and admin pages. ORM count excludes separately declared Intraday tables and is not a deployed database count.

Inspected concentration of responsibility:

| File | Lines at inspection | Implication |
|---|---:|---|
| `market-data/src/services/scheduler.py` | 16,831 | Scheduling, workflows and business logic are heavily co-located |
| `market-data/src/services/paper_trading_engine.py` | 7,673 | Entry, portfolio management and broker concerns share a large module |
| `market-data/src/api/routes.py` | 6,393 | Broad market and calculation concerns remain in one router |
| `frontend/src/lib/api.ts` | 4,974 | Large shared frontend API/type boundary |
| `shared/db/models.py` | 4,768 | Broad shared schema couples releases |
| `frontend/src/pages/_app.tsx` | 859 | Global shell contains navigation, session and other application behavior |

Line counts are maintenance indicators, not quality scores. Existing extraction into smaller modules is valuable and should continue by domain, not arbitrary file-size targets. Current startup source calls `init_db()` in multiple services; historical comments claiming all twelve do so must not be treated as today's verified count.

This review used source, repository design records and the saved October trading-workspace desktop screenshot. That screenshot is a sample-data artifact, not a live browser test. Runtime flags, installed production package versions, backup schedules, data entitlements, broker behavior and current service objectives remain unverified. No penetration test, load test, restore or new full test run was performed in this extension. Earlier targeted tests are recorded separately in [evidence.md](evidence.md).

## What's good, what's weak, what to do

| Area | Good foundations | Weakness or institutional gap | Decision |
|---|---|---|---|
| Market discovery | Rankings, screeners, watchlists, heatmap, sectors, market pulse | Several entry points without one ranked workflow or consistent readiness | Keep engines; combine discovery experience with explicit scope and presets |
| Stock analysis | Chart, signals, technical structure, game plans, goals, report context | Multiple apparent answers to entry/size; information density | Make one selected plan authoritative; move supporting evidence into tabs/drawers |
| AI/ML | Suppression, calibration diagnostics, provenance and research guards | Final evaluation boundary and trust issues from earlier audit; no demonstrated universal alpha | Complete evidence qualification before expanding model complexity |
| Fundamental research | Filing facts, valuation scenarios, quality/value qualification, counterevidence | Broad framework can look more complete than symbol-specific coverage | Display instrument-specific coverage; preserve scenarios and unknowns |
| News/events | Dedicated ingestion, classification, filings, earnings/macro | Event identity/time and alert fragmentation; slow/fast context mixed | Canonical event dossier and material-change feeds |
| Options/flow | Structures, payoff calculator, contract checks, flow and historical studies | Objective matching can be mistaken for return prediction; many specialist destinations | One options desk linked to an underlying thesis and portfolio |
| Portfolio/risk | Positions, optimizer, VaR/CVaR, stress tests, exposure reservations | Multiple position concepts and limited generic analytics semantics | Consolidated account book and explicit analytics coverage |
| Execution | Broker abstraction, order history, submission state/reconciliation mechanisms | Paper and broker concepts coupled; safer submit path is flag-dependent | Separate simulation/live books and qualify full order lifecycle |
| Alerts | Outbox, preferences, delivery receipts, deduplication | Signal/news/risk/condition alerts compete for attention and differ in attribution | One alert policy/inbox over distinct source events |
| Copilot | Evidence packets, typed claims, bounded drafts, explicit user saves | Can become another answer surface if plan/evidence identity diverges | Keep as a contextual assistant to the same selected plan |
| Custom strategies | JSON rule-tree interpreter rather than arbitrary `eval()` | Research execution semantics and resource boundaries still need institutional qualification | Versioned research workbench, not instant live strategy deployment |
| Workspaces | Reorder/hide/resize, focus mode, revision-aware saved preferences | Customization cannot solve poor default hierarchy; cluttered global navigation | Preserve engine; ship purposeful default workspaces |
| Identity/security | bcrypt, backend ownership/admin checks, revocation mechanisms, encrypted broker configuration | Session lifecycle, key separation, dependency maintenance and privileged-action controls | Strengthen before broadening live use or multi-user access |
| Operations | Deploy locks, drift tooling, bounded logs, database guards, safer dump publication | Shared failure domain, mixed backup mechanisms, unverified restore objectives | Explicit workload isolation and recovery proof |
| Testing/release | Backend/frontend CI and mandatory PostgreSQL outbox checks | Test presence does not establish every release gate or production parity | Capability-specific release evidence and reproducible builds |
| Education | Many specialist guides and contextual labels | General Learning is advanced-tier gated; content/status drift | Basic education for all authorized users; deeper material contextually linked |

## Platform findings and reprioritized known gaps

These supplement A01–A18 in the feature audit. Priorities concern reliance on the affected capability, not inferred historical losses or breaches.

### P01 — High: live-order safety depends on a flag, not merely on installed code

[`broker_submission.py`](../../services/market-data/src/services/broker_submission.py) implements durable intent, submitting/unknown states, identity and reconciliation. `submit_after_commit_enabled` returns false when its Redis setting is absent/unreadable; the source explicitly retains historical inline behavior. [`paper_trading_engine.py`](../../services/market-data/src/services/paper_trading_engine.py) branches between marking deferred intent and `_place_broker_entry`.

**Good:** unknown acceptance is explicit and not blindly retried. **Risk:** inspecting this new module cannot establish that production uses the safer lifecycle. An order accepted before its local transaction commits can outlive a rollback. The module documents this failure boundary; this review did not induce it against a broker.

**Required:** inventory active flag, adapters and accounts; qualify durable intent as the required live capability; reconcile pending/unknown exposure across crashes, partial fills and operator recovery. Unknown configuration should not silently choose a weaker live path. Changing that default needs a controlled execution migration, not a cosmetic cleanup. Do not switch it during this documentation task.

### P02 — High: self-service password change leaves other sessions valid

[`auth.change_password`](../../services/market-data/src/api/auth.py) updates the hash without revoking existing tokens, explicitly noting the missing fresh-token client flow. Admin/public resets already use revocation. Someone changing a compromised password can reasonably expect other sessions to end, but this path does not ensure it.

**Classification:** known gap raised in priority, not a newly discovered defect. The existing R07 comment documents the deliberate omission pending a frontend fresh-session/token flow.

**Required:** invalidate earlier sessions and issue/establish a new current session atomically enough to avoid restoring revoked access. Add active-session inventory, revoke-all, step-up authentication for broker connections/live activation and bounded admin impersonation. Test both current-device continuity and rejection of the prior token across every backend validator. This is source-confirmed behavior, not evidence of account compromise.

### P03 — High: session exposure and encryption-key coupling

[`frontend/src/lib/auth.ts`](../../frontend/src/lib/auth.ts) stores JWTs in localStorage, including the original admin token during impersonation. That creates token-theft exposure if same-origin script execution is compromised; it does not prove an existing XSS exploit. [`broker.py`](../../services/market-data/src/api/broker.py) encrypts credentials with a Fernet key derived from the JWT secret and supports legacy plaintext rows. Current production row formats were not inspected.

Possession of the signing secret plus the encrypted configuration blobs permits their decryption. Replacing that secret without retaining/migrating the old encryption key makes previously encrypted configurations unreadable through the current decryptor; legacy plaintext rows are a separate case. Rotation must therefore include a credential migration/recovery plan.

**Required:** design a secure session boundary with short-lived access and rotated/revocable sessions; evaluate HttpOnly/Secure cookie-backed sessions with proper CSRF protection and origin controls. Separate credential encryption keys from token signing, add key IDs/rotation and restricted decryption access, inventory legacy rows without exposing their contents. Preserve recoverability during rotation. Model privileged impersonation explicitly with actor/subject and reason; never infer authority from a hidden menu. Use [OWASP ASVS](https://asvs.owasp.org/) as a verification framework, not a claimed certification.

### P04 — High: dependency maintenance needs an explicit release gate

[`frontend/package.json`](../../frontend/package.json) pins Next.js `14.2.5`; matching version entries exist in the lockfile. The official [middleware-bypass postmortem](https://vercel.com/blog/postmortem-on-next-js-middleware-bypass) documents a historical fix at `14.2.25`. That patch is a historical lower bound, not today's recommended version. No middleware file was found in the inspected frontend listing, and backend authorization is separate, so this review does **not** establish exploitability of that particular CVE here.

**Required:** inventory actual deployed dependencies and all applicable advisories, select a currently supported patched release, test framework/runtime upgrades, generate an SBOM, and block unaccepted high-risk findings. Do not declare the application secure by applying one old patch or by relying on a frontend gate cookie.

### P05 — High for risk interpretation: risk and stress-test endpoints are not a full account risk book

Both `portfolio_risk` and `portfolio_stress_test` in [`portfolio-optimizer/src/api/risk.py`](../../services/portfolio-optimizer/src/api/risk.py) accept at most ten requested symbols, apply `abs()` to supplied weights, and re-normalize weights over symbols with available return history. The risk response names the available symbols and weights but does not expose an explicit missing-exposure coverage measure. Both choose HSI only when HK symbols are a strict majority of the available symbols (`hk_count > len(available) // 2`); ties and nonmajorities use SPY. The comment claiming “any HK stock” is stale. Selection is by symbol count, not exposure weight.

**Implication:** these endpoints perform long-only covered-subset analysis, not a reliable representation of an arbitrary long/short, cash-heavy, mixed-currency, options-inclusive account. For example, a negative hedge weight becomes positive; if one holding lacks history, the rest are scaled to 100%. This finding is scoped to these two routes, not every portfolio calculation in the repository.

**Benchmark consequences:** exposure weights do not participate in the vote: a portfolio with 90% in one HK stock and 10% across two US stocks still selects SPY when all three have history. Only `available` symbols vote, so missing history can change both the represented exposure and the benchmark. For example, two HK and two US symbols select SPY; if one US symbol lacks history, the remaining two HK and one US symbols select HSI. This is a change caused by data coverage, not a deliberate change in the investor's allocation.

The formula alone would select HSI for one HK symbol, but neither endpoint supports that as a successful single-symbol request: both require at least two requested symbols and at least two symbols with available history. The stale “any HK stock” source comment remains unchanged in this documentation-only review.

**Required:** a future correction should explicitly reject unsupported signed exposures; disclose covered/uncovered gross/net exposure and dates; eventually use signed positions, cash, FX and derivative risk with portfolio-appropriate benchmarks. Missing assets must remain visible instead of creating an apparently complete risk answer. Use fallback stresses where historical modeling is unavailable and label them separately.

### P06 — Medium: investment and operational concepts are scattered across navigation

[`_app.tsx`](../../frontend/src/pages/_app.tsx) organizes Markets, Reports, Research, Options Flow, Portfolio, Tools, Learning and Admin with many individual links. Reports repeats market/valuation/discovery context; Tools includes risk/decision/macro features; paper and diagnostic workflows sit in Admin. This reflects organic growth rather than one user journey.

The saved [workspace screenshot](../features/assets/trading-workspace/trading-workspace-desktop.png) shows a promising chart-plus-plan layout, but also competing top-level signal/fair-value cards and lower AI/confluence/consensus panels, alongside several refresh and arrangement controls. This is visual evidence about that saved sample, not a measured usability failure on the live release.

**Required:** reduce primary destinations, preserve specialist tools behind task tabs and searchable commands, keep account/environment/horizon persistent, and test default workflows with actual users. Never delete a page solely because it has low traffic before checking access and discoverability.

### P07 — Medium: foundational learning is restricted while complexity remains broad

The Learning navigation group uses `minTier: 'advanced'`, and auth helpers support matching page guards. A novice who can see research/market tools benefits from basic definitions and risk explanations. **Proposed product decision:** make foundational education and explanations available to all authenticated tiers; retain capability restrictions on advanced actions/data separately. Entitlements are not changed by this document.

### P08 — Medium: backup existence is not recovery assurance

[`pg_backup.sh`](../../scripts/pg_backup.sh) has lock coordination, disk checks, partial-file publication and dump completion validation. [`backup_db.sh`](../../scripts/backup_db.sh) is another S3-oriented workflow with its own settings, while the weekly downloader has separate retention concerns. These scripts establish capabilities, not which schedules run or whether restoration meets any objective.

**Required:** one declared backup owner/pipeline with independent storage, encryption, retention, monitored age and restore drills. Include database, qualified model artifacts, policy/selection identities, required object storage and key-recovery procedures. Define RPO/RTO by business capability. Database recovery must reconcile against broker truth before resuming orders; restoring an old “unsent” state can otherwise replay a real accepted order.

### P09 — Medium: many services share a database and host failure domain

Compose provides useful modular deployment, loopback binding and bounded logs, but shared storage/schema and a large market-data workload create coupling. The inspected compose limits ML resources; it does not prove host-level capacity or adequate isolation of every workload. Startup DDL guards mitigate contention; they do not make schema changes a clean independently governed release step.

**Required:** first separate critical position/order work from optional research/training/backfills by queues, worker quotas and cancellation boundaries within the existing architecture. Establish module ownership and a one-shot migration owner. Split deployment units only where fault isolation, measured load or release cadence warrants it. Kubernetes or a service rewrite is not a prerequisite.

### P10 — Medium: capability/status needs a single source of truth

Existing source has research-only calibration, disabled Intraday production generation, staged broker submission and symbol-specific valuation qualification. Static “live,” “AI,” “new” or “self-tuning” labels cannot convey these differences.

**Required:** a capability registry describing implemented/deployed/enabled/data-qualified/execution-qualified states, scope, owner, last verification and blockers. UI, Copilot and admin pages consume the same record. Validated for one symbol, horizon, provider or account must not become platform-wide validation.

## Institutional capability map

This is a readiness assessment by evidence, not a fabricated numeric maturity score.

| Capability | Current assessment | Target evidence |
|---|---|---|
| Research breadth | Strong breadth; uneven qualification | Source/date/assumption and coverage on every material claim |
| Decision consistency | Partial; known duplicate policies | Same snapshot produces the same verdict in UI, alerts, simulation and qualified execution |
| Point-in-time data | Multiple useful foundations; incomplete universal contract | Available-at/basis/revision/universe lineage and reproducible snapshots |
| Model governance | Partial with substantial safety work | Qualified evaluation protocol, registry, publication fencing, monitored rollback |
| Portfolio accounting | Useful holdings tools; canonical institution-style book not established | Reconciled positions/cash/fills/fees/corporate actions by account and currency |
| Order management | Implemented mechanisms, activation uncertain | Durable intent, controlled retries, idempotency limits, partial fills and reconciliation proven |
| Risk management | Useful calculations/limits; coverage semantics incomplete | Pre-trade aggregate limits, post-trade monitoring, unknown-exposure handling and stresses |
| User experience | Strong configurable workspace; navigation fragmented | Reliable default journeys with fewer context switches and explicit state |
| Security | Foundational controls with confirmed gaps | Threat model, verified sessions/permissions/key lifecycle and patch process |
| Recovery/operations | Good incident-driven tooling; objectives unverified | Tested recovery targets and observed decision-level service quality |
| Collaboration | Individual-user ownership exists | Research sharing/review permissions only if a real multi-user need arises |
| Return advantage | Not demonstrated platform-wide | Strategy-specific unseen net results and forward evidence at realistic execution assumptions |

## Features worth pursuing

The most valuable additions are connected workflows, not another list of indicators:

1. **Unified opportunity queue:** ranked candidates with why now, decisive blocker, freshness and intended horizon; watch/research/ready distinctions.
2. **Investment dossier:** one instrument identity linking thesis, events, financial evidence, chart setup, valuation scenarios, notes and prior decisions.
3. **Trade readiness and plan lifecycle:** the earlier audit's trigger, ceiling, expiry and invalidation, attached to the selected account and risk policy.
4. **Reconciliation and exception desk:** unknown broker orders, stale positions, incomplete cash/FX and pending corporate actions, with evidence-led resolution.
5. **Portfolio risk cockpit:** aggregate exposures, factor/sector concentration, cash and options scenarios with a visible coverage denominator.
6. **Decision replay and attribution:** distinguish idea quality, entry delay, costs, sizing and exits; include vetoed opportunities without hindsight.
7. **Research/model registry:** experiment versions, tested variants, data provenance, evaluation protocol and promotion/rollback history.
8. **Actionable event timeline:** what changed, which holdings/plans are affected, expected versus actual where evidence exists.
9. **Contextual Copilot:** explain the same selected evidence and draft bounded changes; never a second hidden decision engine.
10. **Learning in context:** definitions, examples and practice next to the tool being used, with basic access independent of advanced trading entitlements.

Defer broad multi-asset expansion, autonomous strategy discovery, tick/order-book features without appropriate data, unrestricted execution agents, social trading and exchange connectivity that the current use case cannot justify. For future team use, add approval/sharing/reporting capabilities after validating actual roles and legal scope; do not claim institutional regulatory compliance from this engineering plan.

## Recommended product direction

Keep the research engines and integration investment. Consolidate presentation, contracts and decision authority. Strengthen account/session/execution/recovery controls before increasing live automation. Release a simpler default workstation while preserving advanced tools through progressive disclosure. Evaluate success using completed user tasks, consistent evidence, controlled risk and net outcomes—not the number of features or green badges.
