# Learning Section — tier-gated playbooks

Created 2026-09-10 by **T382-CLAUDEMD-REINDEX**: this content had NO topic file and lived
only in `.claude/CLAUDE.md`'s index, so it was re-read every session with nowhere to grow.

the whole Learning nav group is now advanced-tier/admin only, enforced by `isGroupVisible()` in `frontend/src/pages/_app.tsx` AND a page-level redirect guard (`hasAdvancedAccess()`, `frontend/src/lib/auth.ts`) on all 7 pages, since nav-hiding alone left them URL-reachable. Two long-form playbook pages added: `frontend/src/pages/qqq-leaps-playbook.tsx` (QQQ vs TQQQ vs QQQM, 0.80-delta mechanics, live-chain case-study math, $10k/$20k sizing) and `frontend/src/pages/squeeze-playbook.tsx` (short vs gamma squeeze — mechanics, comparison, execution playbook, and the platform's own measured alert win rates with their sample-size caveats). Both deliberately state where measured edge is weak/negative rather than asserting profitability. Tracker tiers 353-354.
