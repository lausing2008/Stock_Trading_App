# Platform audit and learning wiki — November 6, 2026

**Documentation only. No application code, configuration, model, trading rule, subscription, or deployment was changed.**

The highest-value next step is a consistent, auditable trade decision: distinguish a promising stock from a timely entry, qualify the underlying evidence, and measure the trade after realistic costs. Adding more indicators before doing this would increase complexity without demonstrating better returns.

| Read | Purpose |
|---|---|
| [Deep platform audit](audit.md) | Current implementation, reproduced defects, remaining evidence gaps, and feature-by-feature recommendations |
| [Architecture and delivery plan](architecture.md) | Consolidation, early-rally workflow, prediction research, interfaces, priorities, acceptance gates, and rollout |
| [Learning wiki](learning-guide.md) | How to use the existing features together, worked examples, mistakes to avoid, and a daily checklist |
| [Evidence and verification](evidence.md) | Source baseline, reproducible witnesses, tests, references, and limits of this review |
| [Whole-platform institutional review](platform-review.md) | Good/weak foundations across the system, ten additional findings and institutional readiness |
| [Consolidation blueprint](consolidation.md) | Existing-page mapping, canonical business contracts, domain ownership and safe migration |
| [Professional workstation UX](experience-design.md) | Six workspaces, default screens, interaction states, contextual learning and usability acceptance |
| [Institutional controls and roadmap](institutional-roadmap.md) | Sixteen controls, twenty prioritized work items, execution/recovery architecture and delivery gates |

Recommended implementation order: **correct semantics → reliable evidence → one entry policy → early-rally research → qualified predictions → capital-efficient execution**. Correctness repairs need not wait for a profitability study; changes claiming better trading performance do.

This is a deep source and research audit of the requested trading features and their shared decision/data paths. It is not a production health certification, penetration test, or a fresh portfolio backtest. Historical production results are explicitly dated. Current live settings, data coverage, and deployed-source parity were not queried.

The wiki is usable now as repository documentation. The existing application already has `/learn`, `/options-dark-pool-tutorial`, `/options-income-guide`, and `/squeeze-playbook`. Integrating this material into those pages is planned in the architecture document; no website or UI code was changed.

For the whole-platform follow-up, start with **[platform-review.md](platform-review.md)**, then read the consolidation and UX specifications. The direction is to preserve useful research depth while unifying decision-making, account truth and user workflows. “Institutional-quality” is a target for engineering and product rigor, not a claim of regulatory certification or proven investment returns.
