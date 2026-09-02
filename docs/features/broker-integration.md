## Feature Reference: T257-BROKER-ORDER-HISTORY — E*Trade Sandbox/Prod Order History (Built 2026-07-17)

**User ask, surfaced mid-session while checking the E*Trade sandbox connection**: "how can I
see all the history from sandbox?" — clarified to mean E*Trade's own order/trade history (not
this app's separate paper-trading history, which already has its own dedicated UI elsewhere).

**What existed already**: `BrokerInterface.list_orders()` was already defined as an optional
method defaulting to `NotImplementedError` (the same pattern used for other broker-specific-
only capabilities), but no concrete broker implemented it, and there was no API route or UI
surface for it at all.

**Implementation**: `EtradeBroker.list_orders(account_id=None, status="open")` calls E*Trade's
real `GET /v1/accounts/{key}/orders.json` — the same endpoint `get_order()` already used with
an `orderId` filter, just called without one to get the full list. An explicit status-vocabulary
map translates this app's internal terms to E*Trade's own literal params (`open`→`OPEN`,
`filled`→`EXECUTED`, `cancelled`→`CANCELLED`, `rejected`→`REJECTED`); `status="all"` omits the
param entirely rather than passing something E*Trade wouldn't recognize (which would silently
return zero rows, not an error). Parses `OrdersResponse.Order[]` into `BrokerOrder` instances;
E*Trade's epoch-millisecond `placedTime` is converted to ISO8601 inside a try/except so a
missing or malformed timestamp degrades to `None` rather than crashing the whole call. Added a
new optional `placed_at` field to the shared `BrokerOrder` dataclass (backward-compatible,
defaults to `None` for every other broker).

**API**: new `GET /broker/connections/{id}/orders` in `services/market-data/src/api/broker.py`
— verifies the connection is authorized, calls `list_orders()`, and specifically distinguishes
`NotImplementedError` (→ HTTP 501, "this broker doesn't support this") from any other failure
(→ HTTP 502, a real error) rather than collapsing both into one generic error response.

**UI**: `frontend/src/pages/settings.tsx` gained an "Order History" button per broker
connection, next to the existing "Load Balance" button. Three distinct states are rendered,
not collapsed into one blank screen: a specific "not supported by this broker" message on a
501, an empty-state message when the account genuinely has zero orders, and a full table
(Symbol/Side/Qty/Status/Filled Price/Placed) otherwise. `frontend/src/lib/api.ts` gained
`brokerOrderHistory()` and the `BrokerOrderHistoryItem` type.

**Tests**: `services/market-data/tests/test_broker_order_history.py`, 9 cases, run directly
against the real `EtradeBroker` class with `requests.get` mocked — `EtradeBroker` only depends
on `requests`/`requests_oauthlib`, both real installed packages (not part of this repo's
`conftest.py` stub list), so no source-text-extraction workaround was needed here. Covers
multi-order parsing, status-vocabulary translation, epoch-ms-to-ISO8601 conversion, graceful
`None` on a missing `placedTime`, `status="open"` correctly mapping to `"OPEN"`, `status="all"`
omitting the param, an HTTP failure raising `RuntimeError`, an empty response returning `[]`
(not `None`), and `ManualBroker` correctly inheriting the base interface's `NotImplementedError`
rather than silently returning empty (which would look identical to "authorized but genuinely
zero orders" to a caller). Adversarially verified the status-mapping test by temporarily
passing the internal vocabulary straight through unmapped and confirming the dedicated test
failed (`'open' == 'OPEN'`) before reverting. `requests_oauthlib` needed a local `pip install`
to run these tests in this dev environment (already a real pinned dependency in
`requirements.txt`, just missing locally — not a stubbed dependency).

**What to check if this looks wrong**: a 501 response means the connected broker type doesn't
implement `list_orders()` (currently only E*Trade does — `ManualBroker`/Fidelity-manual does
not, by design, since it has no real API at all); a 502 means the E*Trade call itself failed —
check `docker logs stockai-market-data-1 --since 10m | grep 'orders'` for the underlying error.

---


## Feature Reference: T230-PORTFOLIO-BROKER-SYNC — Automatic Broker Position Sync (Built 2026-07-18)

**Gap this closes**: `GET /connections/{id}/account` (`src/api/broker.py`) already round-trips a
real broker's live positions end-to-end — the whole OAuth + fetch + parse chain already worked.
Nothing ever PERSISTED that fetch into `UserPosition` (`positions.tsx`'s actual data source),
so every broker-linked user still had to hand-copy their real E*Trade holdings into the manual
positions tracker. This was originally tracked as a critical/XL item ("complete a broker
integration sprint") — re-scoping against the actual code before building found the hard parts
already done, shrinking it to "call the already-working fetch and persist the result."

**New function**: `sync_broker_positions()` in
`services/market-data/src/services/paper_trading_engine.py`, piggybacking on the SAME
already-scheduled/locked cycle `poll_broker_order_fills()` runs on inside
`_run_paper_trading_step()` (`scheduler.py`) — no new cron job, no new Redis lock.

**Provenance marker, not a separate table**: `UserPosition` gained two nullable columns —
`broker_connection_id` (FK to `broker_connections`, `ON DELETE SET NULL`) and
`broker_synced_at`. `NULL` = manually entered (every existing row, unchanged behavior).
Non-`NULL` = owned by that sync; the row will be silently overwritten on the next cycle if
hand-edited, which is exactly why the manual CRUD routes now reject edits to it (see below).

**Conflict semantics — the one real risk this design has to get right**: a symbol the sync
wants to write is only ever created fresh (no existing row) or updated in place (existing row
already owned by THIS connection). A manual entry (`broker_connection_id IS NULL`) or a row
owned by a DIFFERENT connection for the same symbol is left **completely untouched** and
logged as a conflict — never silently overwritten with the broker's numbers, since the user's
manually-tracked cost basis/share count could genuinely differ (e.g. a partial manual entry
made before ever linking the account). A synced row whose symbol the broker no longer reports
(sold externally, e.g. directly on E*Trade's own site) is removed — but ONLY rows this sync
itself owns; a manual row is never auto-removed just because the broker reports nothing for it.

**API + UI**: `positions.py`'s `buy`/`sell`/`remove` endpoints now return `409` on a
broker-synced row ("this position is synced from a linked broker account... manage it through
your broker instead") rather than silently accepting an edit the next sync cycle would just
revert. `positions.tsx` shows a "SYNCED" badge next to the symbol and hides the BUY/SELL/remove
controls for those rows (the ★ watch and trade-history-expand controls stay — those aren't
broker-owned state).

**Tests**: `services/market-data/tests/test_broker_position_sync.py`, 10 cases, against a real
in-memory SQLite session + the real `shared/db/models.py` — `paper_trading_engine.py` can't be
imported directly in this test environment (`conftest.py` stubs `sqlalchemy` itself as a
`MagicMock`), so the test pops the stub, builds ONE shared engine, then restores the stub
immediately. **A real test-isolation bug was caught and fixed while writing these**: an
earlier version of this technique left the real `sqlalchemy` swapped in globally for the rest
of the pytest session, silently breaking 7 OTHER test files' collection (they passed in
isolation, failed only in the full suite) — fixed by building the engine BEFORE restoring the
stub (`sqlalchemy`'s `create_engine()` does a dynamic dialect-plugin lookup at CALL time, not
just import time, so it can't be deferred past the restore point) and sharing that one engine
across all 10 tests with a per-test row cleanup instead of a fresh engine each time.

Adversarially verified by disabling BOTH conflict guards (manual-row, different-connection)
simultaneously and confirming 3 tests correctly failed (a real data-loss scenario) before
reverting — disabling just ONE guard alone was insufficient to trigger any test failure, since
the two guards turned out to be redundant-safe (a bug in one doesn't cause data loss because
the other still catches it via its own `!= conn.id` branch). That's a genuinely good defensive
property, caught only by investigating why the single-guard sabotage didn't produce the
expected failure rather than assuming the test was simply wrong.

**What to check if this looks wrong**:
```bash
docker logs stockai-market-data-1 --since 1h | grep 'broker.position_sync'
# broker.position_sync_done {synced, conflicts} on a normal cycle with active connections;
# broker.position_sync_conflict_skipped per-symbol if a manual/other-connection row blocked a write;
# broker.position_sync_error only on a genuine unexpected failure (fetch failures for one
# connection are caught per-connection and don't abort the whole sync — check
# broker.position_sync_fetch_failed for those instead).

# Check a specific user's positions and their provenance directly:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, shares, avg_cost, broker_connection_id, broker_synced_at FROM user_positions WHERE user_id = <id>;"
```

---


## Feature Reference: TIER84-BROKER-ALPACA + TIER84-BROKER-PORTABILITY — Alpaca Broker Adapter + Metadata-Driven Broker Registry (2026-08-18)

**Continues the next-improvements survey** after Tier 287 — verified `docs/improvements.tsx`'s
`TIER84-BROKER-ALPACA` `todo` entry against real code (confirmed: only `EtradeBroker`/
`ManualBroker` existed, zero `AlpacaBroker` class anywhere) before building anything. A mid-turn
user request ("Make the broker integration more portable so that it will be easier to plug with
different brokers like charles schwab, etrade, fidelity etc") arrived while Alpaca was mid-build
and directly shaped its design — rather than building Alpaca as a 4th hardcoded special case,
this session generalized the whole broker-registration surface at the same time.

### Alpaca adapter

**New `services/market-data/src/services/broker/alpaca_broker.py`** — a full
`BrokerInterface` implementation using Alpaca's Trading API v2 (`paper-api.alpaca.markets` /
`api.alpaca.markets`), header-based `APCA-API-KEY-ID`/`APCA-API-SECRET-KEY` auth, no OAuth flow
at all. Two genuinely new architectural properties relative to `EtradeBroker`:
1. **No daily re-auth** — the whole reason this was named "the structural answer" in the
   earlier T257-ETRADE-PROD-SYSTEMATIC entry: a key/secret pair works immediately and never
   expires on its own (only if the user revokes/rotates it at Alpaca's own dashboard).
2. **Split trading/market-data hosts** — `get_quote()` hits `data.alpaca.markets`, a
   completely separate host from the trading API — Alpaca's own documented architecture, not a
   mistake carried over from E*Trade's single-host design.

Order-status vocabulary (`new`/`accepted`/`partially_filled`/`filled`/`canceled`/`expired`/
`rejected`/etc. — Alpaca has ~14 real states) collapsed to this app's existing 5-state
vocabulary (`pending`/`partially_filled`/`filled`/`cancelled`/`rejected`), matching
`EtradeBroker.list_orders()`'s own established status-mapping convention. `get_quote()`
approximates `last_price` as the bid/ask midpoint (Alpaca's `quotes/latest` endpoint returns no
last-trade field at all, unlike E*Trade) — `None` when either side is missing, never fabricated
from a one-sided quote.

**Scheduler infrastructure needed ZERO changes** — confirmed by reading
`_renew_broker_tokens()` (`scheduler.py`) before touching anything: it already does
`if not conn.broker_type.startswith("etrade"): continue` (a non-E*Trade broker is correctly
skipped, since `renew_access_token()` is an OAuth1-only concept), and `_check_broker_auth()`'s
health check calls the fully-generic `broker.get_account()` + `_is_token_rejected_error()`'s
own generic `"401"`/`"unauthorized"` substring match — both already broker-agnostic.

**Upfront credential validation at connection-creation time** — unlike E*Trade (whose OAuth
flow itself validates consumer key/secret at the authorize step) or `fidelity_manual` (no real
credentials to validate), a key/secret-only broker has no separate authorize step where a
typo'd credential would surface. `create_connection()` now calls a live `get_account()` at
creation time for any `AuthStyle.KEY_SECRET` broker, catching a bad key/secret same-session
instead of leaving it silently "authorized" until the next 08:30 ET health check.

### Portability generalization (TIER84-BROKER-PORTABILITY)

**The problem this closes**: before this session, `api/broker.py` had its own hardcoded
`_SUPPORTED_TYPES` tuple, a per-broker-type `if`/`elif` chain building `config` inside
`create_connection()`, and 3 separate OAuth-route guards each independently checking
`broker_type not in ("etrade", "etrade_sandbox")`. Adding Alpaca as a 4th broker meant touching
all of these — exactly the kind of parallel-hardcoded-list drift risk this repo's own broker
registry pattern (`docker cp` file lists, feature-flag touch-points, etc.) already avoids
elsewhere. Generalized so a FUTURE broker (Schwab, a real Fidelity API if one ever ships) needs
only: write an adapter class implementing `BrokerInterface` + declare 3 class attributes +
register it in one list — zero changes to `api/broker.py`'s routes or `settings.tsx`'s form.

**New `BrokerInterface` class attributes** (`interface.py`):
- `BROKER_TYPES: tuple[str, ...]` — the `broker_type` string(s) this class handles (e.g.
  `EtradeBroker` handles BOTH `"etrade"`/`"etrade_sandbox"` — one class, two type strings
  differing only by a constructor flag).
- `AUTH_STYLE: AuthStyle` — a new 3-value enum (`OAUTH1`/`KEY_SECRET`/`MANUAL`) driving which
  routes/UI a connection of this type needs.
- `CONFIG_FIELDS: tuple[ConfigField, ...]` — the credential fields `CreateBrokerRequest` must
  validate and the frontend must render (`key`/`label`/`secret`/`placeholder` each).

**New registry in `broker/__init__.py`** — `_ADAPTER_CLASSES` (the one list to edit for a new
broker), `broker_class_for_type()`, `broker_metadata()`, `SUPPORTED_BROKER_TYPES` (derived, not
hand-maintained), and a `get_broker()` factory that still handles the one genuinely
irreducible per-broker difference (each adapter's own sandbox/paper constructor flag, under a
different keyword name per broker — E*Trade calls it "sandbox", Alpaca calls it "paper";
correctly NOT force-unified into an identical signature that would fight each broker's own
natural terminology).

**`api/broker.py` changes**:
- `_SUPPORTED_TYPES` is now `from src.services.broker import SUPPORTED_BROKER_TYPES` — a
  direct import, not a hand-copied duplicate.
- `create_connection()`'s config-building loop iterates `broker_metadata(body.broker_type)
  ["config_fields"]` generically (`getattr(body, field.key)` + a required-field check) instead
  of a per-type `if`/`elif`. `fidelity_manual`'s `account_number`/`notes` remain a small,
  targeted special case (optional display metadata, not real credentials — `ManualBroker.
  CONFIG_FIELDS` is deliberately left EMPTY so the generic required-field loop never wrongly
  demands one).
- The 3 OAuth routes (`oauth_start`/`oauth_complete`/`reconnect`) now guard on
  `broker_class_for_type(conn.broker_type).AUTH_STYLE != AuthStyle.OAUTH1` instead of the old
  hardcoded `("etrade", "etrade_sandbox")` tuple — a future OAuth1 broker's connections would
  correctly pass this guard without an `api/broker.py` edit. The actual `start_oauth()`/
  `complete_oauth()`/`renew_access_token()` calls inside these routes remain `EtradeBroker`-
  specific (those 3 methods live on the class itself, not `BrokerInterface` — correctly NOT
  abstracted further with only one real OAuth1 implementation to generalize against).
- New `GET /broker/types` endpoint (admin-only, matching every other route in this file per
  `T270-BROKER-ADMIN-GATE`) returns every registered broker's metadata as plain JSON.

**Frontend (`settings.tsx`)**: fetches `GET /broker/types` alongside the existing
`brokerList()` call. The credential-field block in "Add Broker Connection" is now generic —
`currentBrokerMeta.config_fields.map(...)` renders each field's label/placeholder/secret-vs-
plain input type, replacing the two hardcoded per-type JSX blocks. `newBrokerKey`/
`newBrokerSecret` state (2 hardcoded fields) replaced with a generic
`newBrokerFields: Record<string, string>` keyed by field key. The broker-type dropdown itself,
the OAuth start/complete/reconnect UI (still correctly gated on `isEtrade`, since only E*Trade
needs it today), and the dedicated `fidelity_manual` account-number field/hint text are all
unchanged — those are either a genuinely small curated list (new broker TYPES still need real
backend registration, so a static dropdown is correct) or broker-specific prose that doesn't
belong in generic metadata.

**A real overlap caught and fixed during implementation, not shipped**: the first draft gave
`ManualBroker.CONFIG_FIELDS` a real `account_number` field entry (matching the "declare your
real fields" pattern every other adapter follows) — but this would have rendered `account_number`
TWICE (once via the new generic block, once via the pre-existing dedicated `fidelity_manual`
JSX block) and made it incorrectly `required` via the generic loop's own validation, when it's
genuinely optional display metadata. Fixed by making `CONFIG_FIELDS` empty for `ManualBroker`
specifically, with a comment explaining why (`account_number`/`notes` are handled as a
targeted special case in `create_connection()`, never through the generic per-field loop).

### Tests

`services/market-data/tests/test_alpaca_broker.py` (28 cases) — `AlpacaBroker` only depends on
`requests` (real, installed, not stubbed), tested directly with `requests.get`/`post`/`delete`
mocked. Fixtures built from Alpaca's own documented, stable v2 API response schemas
(`docs.alpaca.markets/reference`) rather than hand-idealized guesses — matching this repo's own
standing lesson (the CAPE-feature entry) that a fixture matching a buggy implementation's own
assumptions can silently certify the bug as correct. Covers account/position parsing, paper-vs-
live base URL + broker_type selection, order placement/status-vocabulary mapping (including the
genuinely-different-from-E*Trade "side is always a plain buy/sell, never a BUY_OPEN/BUY_CLOSE
options variant" property), the split trading/data-API hosts, bid/ask-midpoint quote computation
with one-sided-quote and missing-symbol fail-soft cases, and `is_market_open()`'s fail-open
fallback on a clock-endpoint error.

`services/market-data/tests/test_broker_registry.py` (21 cases) — the registry's own behavior
(`SUPPORTED_BROKER_TYPES` completeness/no-duplicates, `broker_class_for_type()` resolution +
unknown-type rejection, `broker_metadata()`'s per-broker `auth_style`/`config_fields` shape,
JSON-serializability, `get_broker()`'s sandbox/paper flag resolution) plus source-text
regression checks for `api/broker.py`'s generalized wiring (the required-field validation loop,
the `AuthStyle.KEY_SECRET` upfront-check, the 3 OAuth routes genuinely gating on `AuthStyle.
OAUTH1` rather than a lingering hardcoded tuple, the new `/broker/types` endpoint).

**Pre-existing `test_broker_admin_gate.py` updated, not broken**: its own
`test_all_12_known_routes_are_still_present` guardrail correctly caught the new
`list_broker_types` route as an untracked 13th `@router.` decorator — added to
`_ALL_ROUTE_FUNCTIONS` and confirmed it's admin-gated like every other route, renamed the test
to drop the now-stale "12" from its name.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted:
1. Removing `AlpacaBroker` from `_ADAPTER_CLASSES` (a forgotten registration) — caught by 6
   tests across both new test files.
2. Removing `create_connection()`'s required-field validation (silently storing an empty
   string instead of raising 400) — caught by a dedicated source-text test.
3. Reverting the `reconnect` route's OAuth guard back to the old hardcoded
   `("etrade", "etrade_sandbox")` tuple — caught by a dedicated source-text test checking all 3
   OAuth routes generically.

Full 1,590-test market-data suite green (up from 1,541); 132-test frontend suite green;
`npx tsc --noEmit` (including `--strict`) clean; a full `next build` compiled all 51 routes
clean; `pyflakes` clean on every touched file (confirmed via `git stash` that the file's 2
pre-existing warnings — `etrade_broker.py`'s unused `date` import, `manual_broker.py`'s unused
`BrokerPosition` import — predate this change).

**Tracker**: `improvements.tsx` — `TIER84-BROKER-ALPACA` flipped to `done`;
`TIER84-BROKER-PORTABILITY` added as a new `done` entry.

**What to check if this looks wrong**:
```bash
# Confirm the registry resolves Alpaca correctly:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.services.broker import SUPPORTED_BROKER_TYPES, broker_metadata
print(SUPPORTED_BROKER_TYPES)
print(broker_metadata('alpaca_paper'))
"

# Confirm the new endpoint works end-to-end (needs an admin JWT):
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'<admin_username>','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://localhost:8001/broker/types', headers={'Authorization': f'Bearer {tok}'}, timeout=10)
print(r.status_code, r.json())
"
```
If a new Alpaca connection fails immediately at creation with "credential check failed", that's
the upfront `AuthStyle.KEY_SECRET` validation working correctly — check the actual key/secret
against Alpaca's own dashboard before assuming this app's code is wrong.

---


## Feature Reference: T286-CONDITIONAL-ORDER — Single-Hop "If TRIGGER Then ACTION" Orders (Built 2026-08-18)

**Closes the last remaining open item from Tier 287** — `T286-CONDITIONAL-ORDER-CHAINS-
DEFERRED`, deliberately left unbuilt in the original 2026-08-17 batch pending its own dedicated
design pass, since it's the one item in that batch that touches the live paper-trading entry
pipeline directly. This session's own design conversation (before any code was written)
deliberately scoped DOWN from the original "conditional order **chains**" ask
(`docs/FEATURE_ROADMAP_PYRAMID_GOALS_2026-08-16.md`'s literal "if X breaks $140, buy Y with
stop at Z") to a materially safer, still-genuinely-useful core: **same-symbol only** (no
cross-symbol triggers), **single-hop only** (no multi-step chained state — a user wanting a
multi-step plan creates several independent orders), and every `buy` action routed through the
**exact same real entry gate** every organic trade already uses — a conditional order only
ever decides WHEN to act on an already-real, already-eligible setup, never WHETHER the setup
itself is valid. Named `ConditionalOrder`, deliberately not "chain," to keep this scoping
visible in the code itself, not just in a design doc.

### The trigger vocabulary — reuses, not reinvents, existing infrastructure

6 metrics (`price`, `rsi`, `volume_ratio`, `signal`, `position_pnl_pct`, `time`), each a
`{"metric", "op": "gte"|"lte"|"eq", "value"}` dict — the SAME JSON-list-of-condition-dicts
shape as `PriceAlert.compound_conditions` (T230-ALERTING-COMPOUND-CONDITIONS), extended with
`trigger_logic: "AND"|"OR"` (PriceAlert's own compound conditions are AND-only; OR is new here,
per an explicit ask for genuine AND/OR support). `rsi`/`signal` read the SAME persisted
(`live=false`) DB signal `check_signal_alerts()`/`_evaluate_compound_conditions()` already read
— a conditional order's "if RSI < 30" shows the identical RSI a `PriceAlert` compound condition
or the stock detail page would. `volume_ratio` reuses the same `get_rvol()` helper. `price` and
`position_pnl_pct` (a new metric — your CURRENT open position's live unrealized P&L% on that
exact portfolio/symbol) and `time` (an HH:MM UTC clock check) are genuinely new, since neither
had an existing equivalent. Every metric **fails closed** on missing/unavailable data — an
order that can't measure its own trigger right now can never fire on incomplete information.

### The action vocabulary

`buy`, `sell_partial` (a fraction 0-1 of current shares), `sell_all`/`close_position` (aliased
to the same full-close handler), `tighten_stop` (a new stop price, monotonic-only — can never
loosen an existing stop, matching every other stop-tightening mechanism in this codebase),
`alert_only` (never touches a position — just sends the notification email).

### The design decision this session spent the most real effort on: how `buy` actually opens a position

**The core problem**: a conditional order's `buy` action needs to open a REAL position at the
exact moment its custom trigger fires — that's the entire point (entering earlier/more
precisely than the organic 5-10min `_scan_for_entries()` scan cycle would, which only reacts
to signal changes, never an arbitrary user-defined price/RSI/volume/time trigger). But the real
position-sizing/opening logic (~250 lines: risk-based sizing, earnings/confidence/research/
consensus/score size multipliers, HK board-lot rounding, the aggregate open-risk cap, the
sector-concentration cap, the cash gate, the actual `PaperTrade` insert) was entirely inlined
inside `_scan_for_entries()`'s own candidate loop — not a separately-callable function.

**Three options were weighed explicitly with the user before writing any code**: (1) properly
extract the sizing/opening block into a reusable helper (real effort + real risk to a critical,
heavily-audited function, but the only way to get full sizing/gate parity), (2) ship a
simplified v1 where `buy` only sends an "would pass the gate" notification with a manual
one-click execute button, (3) drop `buy` entirely, conditional orders only manage existing
positions. **The user chose (1)** after being shown the real complexity discovered mid-
investigation (`regime_size_mult` — one of the sizing multipliers — depends on cycle-level
`live_regime`/market-breadth data, not just candidate-level state, meaning a clean extraction
needed several more parameters threaded through than a simple "move this block" operation).

**The extraction performed**: a new `_open_paper_trade()` function in `paper_trading_engine.py`,
containing the ~250-line block moved **verbatim** — same variable names, same order of checks,
same audit-comment history (T188, PT-B10, PT-D2, INT-3, AUD262-*, AUD232-*, etc. all preserved
untouched) — with each original `continue` (skip this candidate, try the next) converted to a
`return None, "<skip_reason>"`. `_scan_for_entries()`'s own loop body was replaced with a call
to this function plus 4 lines of cycle-level bookkeeping (`open_symbols.add`, `entries_made
+= 1`, `equity` recompute) it still owns — `_scan_for_entries()`'s own observable behavior is
completely unchanged; it's the SAME code, just relocated so a second caller can reuse it.

**`buy`'s own action handler** (`_execute_buy()` in the new `conditional_orders.py`) resolves
the same portfolio-level context `_scan_for_entries()` computes once per scan cycle
(`_compute_equity`, `_recent_win_rate`, `_consec_loss_streak`, the regime-size-multiplier
lookup from the portfolio's own persisted `regime_state`) for just this ONE candidate, calls
`_call_decision_engine()` (falling back to `_should_enter()` if DE is unreachable — the exact
same dual-scorer pattern `_scan_for_entries()` itself uses), and only on a real, passing verdict
calls the newly-extracted `_open_paper_trade()`. A **deliberate, disclosed scope-narrowing**:
some optional DE parameters (`open_sector_counts`, `market_open_count`, `short_signal`,
`recent_stop_count`) are left at their `None`/default fail-open values rather than exactly
reconstructed — confirmed safe via `_call_decision_engine()`'s own conditional-inclusion
pattern (`if X is not None`), matching this codebase's established convention that a missing
optional field means "gate not applicable here," never a bypass of a mandatory check. Every
HARD circuit breaker (drawdown, daily/weekly loss, confidence floor, R:R ratio) still fully
applies via the real `equity`/`recent_win_rate`/`consec_losses` values that ARE computed exactly.

**A critical, deliberate safety invariant**: `_execute_buy()` never fabricates a signal. It
requires a REAL, already-persisted `Signal` row with `signal == BUY` for the target symbol
before doing anything else — a conditional order only ever decides **when** to act on an
already-real, already-eligible setup, never **whether** the setup itself is valid. A user
"buy if price breaks $140" order does nothing at all if no real BUY signal exists for that
symbol yet, regardless of price — it fails with an explicit, honest reason, not a fabricated
entry.

### A real regression this extraction caught — and confirmed was NOT a behavioral bug

Running the FULL market-data test suite after the extraction (not just the new test file)
surfaced 7 failures in a PRE-EXISTING test file, `test_score_size_mult_gate_source_parity.py` —
exactly the kind of catch this repo's own "run the whole suite, not just your new tests"
discipline exists for. Root cause: that file uses **source-text extraction** (reading the real
`score_size_mult` computation directly out of `paper_trading_engine.py` and `exec()`-ing it
against synthetic inputs) with a HARDCODED dedent amount (8 spaces) matching the code's
original nesting depth inside `_scan_for_entries()`'s own `for` loop. The extraction moved this
same code to 4-space nesting (a plain function body, not a loop) — the underlying
`score_size_mult` FORMULA is byte-for-byte unchanged (verified: `pyflakes`/full-suite-diff
confirm no logic changed, only file location), but the test's own dedent constant needed
updating to match. Fixed by changing the dedent from 8 to 4 spaces and correcting the file's
own docstring references from `_scan_for_entries()` to `_open_paper_trade()`. This is the
one and only test that needed changing anywhere in the full 1604-test suite — everything else
passed with zero modification, a strong signal the extraction was genuinely faithful.

### Scheduler wiring, API, email

`check_conditional_orders()` — 1-minute interval job (`services/market-data/src/services/
conditional_orders.py`), registered right after `check_volume_anomalies` in `scheduler.py`.
**Deliberately fails CLOSED on a Redis lock-acquire failure** — unlike most other 1-minute
alert jobs in this codebase (`check_price_alerts` et al., which fail OPEN, accepting a rare
double-send risk for a passive notification), this feature places real trades, so skipping one
cycle is always the safer choice than risking a double-fire. Per-order try/except isolation
(one order's evaluation failure doesn't abort the rest of that cycle's batch), matching this
codebase's established per-recipient/per-order isolation convention.

`POST/GET/DELETE /conditional-orders` (`services/market-data/src/api/conditional_orders.py`) —
portfolio-scoped (no `user_id` on the model at all, since `PaperPortfolio` itself has none —
paper portfolios are app-wide, not per-user, a fact this codebase has documented repeatedly).
No PUT/edit endpoint — matches the single-hop design: an order the user wants changed is
cancelled and recreated, never mutated in place, keeping "what does this order actually do"
always readable from its own row with no hidden edit history.

`send_conditional_order_email()` — sent on EVERY fire, success or failure, matching this
codebase's own "a silent failure defeats the purpose of an unattended trigger" discipline
already established for other alert types.

### Frontend

New `/conditional-orders` (create/list/cancel page, portfolio dropdown + a dynamic condition-
builder form) and `/conditional-orders-guide` (a dedicated documentation page, per an explicit
user request — matches `alerts-guide.tsx`'s own established `Callout`/`Code`/`WorkflowDiagram`
visual language, not a new one-off style). Nav entries added: the guide under "Learning"
(alongside `alerts-guide`), the management page under "Admin" (alongside `Paper Portfolio`,
since conditional orders modify a specific portfolio's real trading behavior).

### Tests

`services/market-data/tests/test_open_paper_trade_extraction.py` (14 cases) — direct behavioral
tests of the newly-extracted `_open_paper_trade()`, covering the risk-sizing formula (hand-
computed expectation, corrected mid-writing for a real miscalculation — see below), every skip-
reason path (invalid stop distance, AVOID/SELL research hard-gate, min-position-value floor,
aggregate open-risk cap, sector concentration cap, sector position-count cap, insufficient
cash), HK board-lot rounding, broker-entry routing (placed only when `broker_connection_id` is
set), and K-Score-at-entry sourcing. A `_FakePaperTrade` capturing class replaces the module's
own `MagicMock`-stubbed `PaperTrade` (this test environment stubs `db` wholesale) so
constructor kwargs become real, assertable attributes instead of opaque mock accesses.

**A real hand-calculation mistake self-caught while writing these tests, not shipped**: the
first version of `test_shares_computed_from_risk_dollar_over_stop_distance` assumed
`confidence_size_mult = 1.0` for a `sig_conf=60` fixture — but the real formula's `sig_conf >=
50` branch actually yields `1.25`, not the `30 <= sig_conf < 50` neutral branch. The test
failed immediately with the real function's actual output (`100.0`, not the hand-miscalculated
`150.0`), which on tracing back turned out to ALSO reflect the `max_position_pct` cap engaging
(a `187.5`-share position at `$100/share` exceeds the fixture's `10%`-of-equity cap, correctly
rounding down to exactly `100.0`) — both a confidence-multiplier mistake AND a missed
downstream cap in the original hand-calculation, caught by the test disagreeing with itself
before being trusted, not assumed correct on the first pass.

`services/market-data/tests/test_conditional_orders.py` (30 cases) — direct behavioral tests
of `_evaluate_one_condition()`/`evaluate_conditions()`/`execute_action()`'s dispatch (all real,
DB-light functions with only `position_pnl_pct`/`volume_ratio`/`rsi`/`signal` metrics touching
a mocked session/HTTP call), plus source-text regression checks on the heavier action-execution
functions' key safety properties (matching `test_should_enter_de_parity.py`'s own established
proportionate-testing precedent for functions whose full DB-dependent behavior is
disproportionate to drive end-to-end locally): the real-BUY-signal requirement, DE/fallback
gate reuse, the already-open-position rejection, the monotonic stop-tightening guard, the
close-flow's cash-credit + `SignalOutcome` writeback, the fail-closed lock convention, expiry
checked before trigger evaluation, and the always-send-email-regardless-of-outcome property.

**Adversarial verification** — 3 sabotage/revert cycles on `conditional_orders.py`, all caught
correctly: reverting the fail-closed lock handling to fail-open (caught by the dedicated
source-text test); removing the `SignalType.BUY` check from `_execute_buy()` (caught by the
dedicated source-text test, with the real assertion diff showing the guard string genuinely
absent — not a false match); flipping `evaluate_conditions()`'s empty-conditions default from
`False` to `True` (caught by the dedicated behavioral test). All 3 reverted and confirmed
byte-identical via `md5sum` before moving on.

Full 1634-test market-data suite green (up from 1604 — 14 new extraction tests + 30 new
conditional-order tests); frontend `npx tsc --noEmit`, the 132-test vitest suite, and a full
`next build` (both `/conditional-orders` and `/conditional-orders-guide` compile cleanly) all
green; `pyflakes` clean on every touched file (confirmed via `git stash` that every remaining
warning predates this session's changes — only line numbers shifted from new code added
earlier in the same files).

**What to check if this looks wrong**:
```bash
# Confirm the scheduler job is registered and running:
docker logs stockai-market-data-1 --since 1h | grep conditional_order

# Check a specific order's real status:
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'lausing','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://localhost:8001/conditional-orders', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print(r.status_code, r.json())
"

# Confirm _open_paper_trade() and _scan_for_entries() are still calling the same function:
docker exec stockai-market-data-1 grep -n "_open_paper_trade(" /app/src/services/paper_trading_engine.py
```
If a `buy` conditional order always fails with "No current BUY-eligible signal," that's
correct, expected behavior — check whether the symbol genuinely has a real, current BUY
signal via `GET /signals/{symbol}?style=SWING&live=false` before assuming the order itself is
broken; the whole point of this design is that it never fabricates one.

---

