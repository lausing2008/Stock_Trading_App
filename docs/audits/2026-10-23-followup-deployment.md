# October 23 follow-up release

**Application release `a1054dcb` is deployed to lausing.com.** The user authorized committing and publicly pushing the reviewed work to `prod` and deploying it. The EC2 checkout was clean at `e0831bb9`, all 15 containers were healthy and 27 GB was free before preparation.

## Scope and build

The complete reviewed work was committed: Phase C volume/OI dominance and shared v7 cache invalidation; intraday chart history and completed daily crossover evidence; W01/W02 deployment verification; W03 regression harness; W04 empty-cohort API/UI behavior; backup/build host locking; dark-pool identity diagnostic; tracker and documentation. The strategy panel now states that contracts are selected heuristically and nearby usable contracts have not been exhaustively searched. No forecast optimizer or trading thresholds were introduced.

Images for market-data, signal-engine and frontend were built on EC2 from the exact pinned commit, under the deployment lock. The frontend used EC2 `.env.production`, not local build output. Old images were tagged and recorded in `/tmp/options-release-images-a1054dcb.txt`; the recreate script retained rollback handling. Only these three services were recreated with `--no-deps --no-build`. Database/schema/shared code and the other ten application backends were unchanged. Existing Oct 22 capture completion was checked before preparation.

## Validation

- Market-data: **5,784 passed, 26 skipped** in the preceding complete reviewed-tree run.
- Signal-engine: **955 passed, 1 skipped**, applicable unchanged source.
- Host/diagnostic scripts: **44 passed**.
- Frontend: final committed tree **828 passed across 70 files**, full typecheck passed; EC2 production build passed.
- Existing options/chain/dark-pool smoke checks passed. All eight calibration endpoints remained research-only with no applied settings; tracked Redis settings were unchanged.
- All **15 containers healthy**. All **12 backend source/shared hashes match** the application commit. Frontend prepared/running build ID: `ghID9kU82xv0akohrUYlL`.
- `/`, `/stock/MU`, `/improvements`, `/signal-accuracy`, `/signal-tuning`: HTTP 200. New stock/tracker code markers and the selection disclosures are served publicly from that build. No interactive browser acceptance is claimed.

[New deployment evidence](evidence/2026-10-23-followup-deployment/) is separate from the original audit and earlier release checks.

## Dominance in production

Authenticated flow reads and the dedicated dominance endpoint return identical same-cache summaries for MU, AMD and AAPL. Their source session at verification is **October 22**, not a claim of live October 23 activity. Coverage is reported complete for the four selected expiry slices; this does not establish the entire options market was captured.

| Stock | Contract volume | Call / put share | OI |
|---|---|---|---|
| MU | Call-heavy | 60.4% / 39.6% | Higher puts |
| AMD | Mixed | 55.0% / 45.0% | Higher calls |
| AAPL | Mixed | 53.3% / 46.7% | Higher calls |

MU demonstrates why volume and OI must remain separate: they favor different sides. Paid premium and buy/sell evidence remain unavailable. OI reporting date is unverified; direction and opening/closing intent are not inferred. Existing cut points and AI score behavior remain unchanged.

## Backup rollout

The old `/home/ec2-user/pg_backup.sh` was preserved as `.pre-lock`; the cron-facing wrapper now executes the versioned `scripts/pg_backup.sh`. The cron schedule is unchanged. A real dump was started after the service rollout through this wrapper, using the shared lock and `.partial` publication. The first real run completed at **2026-10-24 01:24:10 UTC** (October 23, 18:24 PDT), exit **0**, publishing **2,502,561,784 bytes** after the gzip and PostgreSQL dump-completion checks. No `.partial` remains; 7 dated backups remain after retention. All 15 containers were healthy afterward. The shared lock prevented overlapping deploy/build work. A live lock probe confirmed a concurrent deployment was refused while this actual backup was running; cron still calls the same wrapper. A new restore drill is not claimed.

## Screenshot and strategy ranking

[Detailed selection review and implementation specification](2026-10-23-strategy-selection-review.md). Current contracts are nearest-strike/preset-expiry illustrations, not joint optimizations over Greeks, liquidity and expected returns. Covered Call can be blank because its selected target contract fails pricing/contract validation, even when a different long-call strike works. Share declarations are a separate eligibility gate. The screenshot does not prove the entire chain has no usable covered call.

A conditional highlight already exists for declared objective, holdings, assignment willingness and resources. It does not currently fuse regime/trend/company outlook or forecast evidence. The next work is candidate-specific reasons, comparable eligible-contract enumeration, horizon-matched market context and executable net-cost validation before an expected-return ranking.

## Remaining boundaries

Dark-pool identity semantics/quarantine, feed gaps, the complete calibration replay, intraday-horizon research, news evaluation and generic exact-tested-commit deployment pinning remain open. This manual rollout was pinned; the generic script still needs that hardening. The tracker marks shipped application correctness/features complete while these research and operational limitations remain explicit. Its backup entry remains in progress: the shipped page predates this first-run result; the successful dump is recorded here, and a new restore drill was not performed.
