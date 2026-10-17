# Production deployment handoff — 2026-10-16

## Current status

The committed Game Plan release is deployed and verified. The user-approved reboot recovered SSH, and the prepared frontend image was installed under the host lock without another production compile. Final checks passed: 15/15 healthy, 0/12 backend drift against the isolated release, packet-v7 smoke, both served plan perspectives and tracker 446–450, guarded routes 401, and public `https://lausing.com/stock/AAPL` 200. Risk delivery remains off. GitHub publication remains blocked by HTTP 403. Concurrent uncommitted work below is not deployed.

The production checkout was advanced to `0a003197` through an authenticated Git bundle because GitHub pushes return HTTP 403. Research-engine was rebuilt and passed the packet-v7 production smoke check. Production stopped responding during the subsequent frontend build; the cause remains unproven. Documentation-only follow-up `1a47d759` exists locally.

The user explicitly answered **“Yes, reboot production and finish deployment”** to the recovery question. AWS accepted `reboot-instances` at 17:29:11 PDT. Initial SSH probes timed out; a subsequent connection confirmed fresh guest uptime and all services restarting. Earlier reboot attempts were rejected before execution because general deployment wording did not explicitly authorize the interruption.

## Ready release and recovery

- Host: `ec2-user@18.205.121.71`; key: `~/Documents/Stock_AI/lausing.pem`.
- Instance: `i-0fb547ee9c6e15739`, region `us-east-1`.
- Verified off-host image: `stockai-frontend:dual-game-plan-0a003197`.
- Archive: `/tmp/stockai-frontend-0a003197.tar.gz`; SHA-256 sidecar: same path plus `.sha256`.
- Isolated source: `/tmp/stockai-release-0a003197`. Use this for release drift checks, not the concurrently edited main worktree.
- Full recovery context: `/tmp/dual-plan-key-recovery-state.md`.
- Production verification: `/tmp/dual-plan-production-verify.py`.

Recovery acquired the actual host deployment lock and verified no old release builder remained. The archive checksum passed; the old frontend image was preserved as `stockai-frontend:rollback-before-dual-game-plan`; frontend alone was recreated with `--no-deps --no-build`. Do not compile frontend on production again for this release.

Acceptance passed for all 15 containers, all 12 backend source hashes, served Game Plan and tracker bundles, guarded routes, and the disabled risk-delivery flag. Database blocking and idle transactions over 60 seconds both measured zero. PostgreSQL reads briefly reached the disk throughput limit after reboot, then the next host samples settled to 0–2.2% disk utilization and 0–0.9% iowait. Keep email delivery off. No orders, enrollments or test emails were created by release verification.

## Concurrent changes for the other session

The main worktree also contains uncommitted CNN Fear & Greed changes in:

- `services/market-data/src/services/fear_greed.py` (new)
- `services/market-data/tests/test_cnn_fear_greed.py` (new)
- `services/market-data/src/api/routes.py`
- `services/market-data/src/services/scheduler.py`
- `frontend/src/lib/api.ts`
- `frontend/src/pages/market-pulse-dashboard.tsx`
- `frontend/src/pages/reports.tsx`
- `frontend/src/pages/stock/[symbol].tsx`
- `docs/FEATURES.md`

These files were inspected and left intact. Their focused suite passed **23 tests**, and `npm run typecheck` passed against the current worktree. This is partial validation, not deployment acceptance. They are not included in the prepared image. The owning session should finish its review, integration checks and commit before preparing a separate release; do not mix mutable worktree files into the recovery image.

The Game Plan release already passed 709 research-engine tests (8 skipped), 615 frontend tests, TypeScript, and 79 PostgreSQL plan tests. These are implementation checks; no paid real-model quality evaluation was performed.
