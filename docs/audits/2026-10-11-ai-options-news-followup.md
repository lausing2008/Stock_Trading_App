# AI signals, options alerts, and Surface RTX Spark follow-up

> **Completion review — 2026-10-24:** Event/funnel/model-quality/research-plan foundations exist. General company shadow coverage is built; live actionable options and proven model-quality improvement remain incomplete. See [current review and remaining work](2026-10-24-alerts-plan-workspace-completion.md). The dated record below is preserved.

Read-only production snapshot: October 11, 2026, 22:32 UTC, revision `58138c29`; all app containers healthy. Queries and sanitized results are in [the evidence directory](evidence/2026-10-11-signal-news-followup). Production observations below differ from the local changes. Provider email acceptance is not proof of inbox delivery.

## Findings

Microsoft's October 7 [Surface announcement](https://blogs.windows.com/devices/2026/10/07/pre-order-our-most-powerful-surface-devices-ever/) confirms Surface Laptop Ultra uses NVIDIA RTX Spark. Production ingested a shipping headline about three seconds after its publication timestamp and tagged MSFT and NVDA. Both were classified positive and material. The shorter launch headline was classified neutral and non-material. The provider also incorrectly tagged `RTX` (RTX Corporation) because it confused NVIDIA's product name with a stock symbol. These headlines did not mention or tag INTC. Surface Laptop Ultra uses NVIDIA Grace CPU rather than an Intel CPU, so INTC has a direct negative design-slot implication for this device. The launch does not establish lost Intel revenue across the full Surface range or a measurable INTC stock decline. Microsoft previously disclosed the NVIDIA design in May/June, so the October 7 event was the preorder and shipping milestone, not the first processor-switch disclosure.

MSFT is active but has zero watchlist subscribers and zero open price alerts. NVDA has three watchers and two open price alerts; INTC has one watcher and four open price alerts. The scheduler has an early earnings news alert but no general product-impact notification route. News ingestion and classification alone therefore did not create the requested event alert or playbook.

Latest signals are current. MSFT LONG/GROWTH and NVDA SHORT/SWING/LONG are BUY; INTC is HOLD across horizons. They are model/technical outputs, not event-specific calls. Strength is `abs(fused_score - 0.5) * 200`, a distance from neutral, not a success probability. The latest signals for all three stocks have the low out-of-sample quality flag. In 24 sampled model artifacts, all 24 were suppressed. Stored metrics show 11 with CV AUC below 0.52, 5 with zero precision and recall, and 20 with a CV/test AUC gap above 0.10; reasons overlap. Many holdout samples contain only 12–27 observations. The artifacts do not persist a single suppression reason, so the counts are derived from metrics. Inflating strength or removing the quality gate would hide that evidence.

Options flow recorded 169 candidates October 5–9. All 169 have `delivery_state = NULL`, so their email acceptance state cannot be reconstructed from those rows. One user currently meets the legacy sender's open-price-alert and email prerequisite. That does not establish an email was sent to or received by that user. The fixed-contract ledger has zero captures; the outbox has zero rows. The `option_opportunity` enqueue helper has no production caller and no explicit opt-in rows exist. Thus the documented options playbooks are research policies, not a live source of sized contract alerts.

## Changes made locally

- Added bounded Microsoft, NVIDIA, and Intel aliases to universe-scoped RSS headline matching. Common headlines omit “Corp.” and “Corporation,” so exact legal-name matching missed them.
- Removed the provider's RTX Corporation tag from future “RTX Spark” product headlines unless the company is explicitly named. Existing stored headlines remain unchanged.
- Clarified in Options Flow Review and alert preferences that actionable opportunity generation is not connected. Enabling the preference alone does not create trade plans.

## Next improvements

1. Add a durable event-impact watch for product launches: first-party source intake, event deduplication, publication timestamps, per-company roles, evidence-backed possible effects, watchlist/subscription rules, and recorded skip/send states. MSFT and NVDA merit potential impact watches; INTC is a displaced CPU supplier for this device; alert it as a negative competitive design-slot watch with unknown revenue and share-price impact. Distinguish the first May/June design announcement from the October preorder milestone. A generic headline sentiment must not become three automatic trade directions.
2. Expose a per-user alert funnel from observed event/flow through eligibility, risk and confirmation gates, queueing, and provider acceptance. Distinguish historical unknown delivery from a confirmed suppression and separate observed flow from actionable opportunities.
3. Connect one supported, long single-leg options playbook to the prospective ledger only after exact contracts, live quotes, entry/exit rules, costs, loss limits, buying power, and capture/resolution evidence are available. Spread and short-option strategies need their own payoff and assignment handling.
4. Surface model suppression reasons, holdout counts, and model age beside strength. Evaluate versioned prospective alert cohorts by market, horizon, and delivery state before tuning fusion or thresholds.

This follow-up made no production alert, historical news, watchlist, trade, or model-artifact changes.

## October 12 implementation follow-up

The [implementation record](../features/2026-10-12-event-impact-flow-funnel-and-options-review.md) describes the local product-impact watch, per-user delivery receipts, model-quality fields, and exact-contract long-option research plan. The initial October 7 story will appear as historical research when the job first sees it; the four-hour gate prevents a late breaking-news email. An actionable option alert still requires live quotes, deliverable terms, account options permission, and verified risk capacity that the current adapters do not provide.
