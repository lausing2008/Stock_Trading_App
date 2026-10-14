# Copilot phase 2: chart commands, screening, and action drafts. Design for review (2026-10-13)

Builds on [phases 0–1](2026-10-13-copilot-evidence-contract.md) (commit `ab3c0dd7`).
**Status: decisions recorded (§5). Implementation in progress, in the order screening → drafts → chart.** Facts below come from a read-only inventory of the current code; file:line
references are as of `ab3c0dd7`.

## 1. What the inventory found

| Area | Current state | Consequence for the copilot |
|---|---|---|
| Chart | `PriceChart` keeps every control in local state: about 17 indicator toggles, range, drawings (`PriceChart.tsx:141-197`). It exposes no ref, no callback, and no `onChange`. Every toggle rebuilds the chart and calls `fitContent()`, which resets the zoom (`:302`, `:798`, `:914`). | The copilot cannot drive the chart without a refactor, and "show me the 50-day" would also reset the user's zoom. |
| Drawings | Saved only in localStorage (`stockai:{user}:chart_drawings:{SYMBOL}`, `lib/chartDrawings.ts`). Per-item delete and "Clear all"; **no undo**. | Copilot drawings need an undo that the chart does not have yet. |
| Price alerts | `POST /alerts` always inserts: no duplicate check, no idempotency key, no per-user cap, and the symbol is not checked against `stocks` (`alerts.py:104-131`). | A retried "save" would create two identical alerts, which breaks the phase 2 gate "idempotent saves". |
| Signal alerts, S/R watch | Both upsert by unique key (`signal_alerts.py:63-71`, `routes.py:2909-2924`). S/R watch **overwrites** an existing row's multiplier and note. | Safe to retry. An S/R draft must show when it would replace an existing watch. |
| Watchlist add | `POST /watchlist/{symbol}` returns the existing item when the symbol is already present, **and silently creates an email signal-alert subscription** for the list's style (`watchlist.py:227-240`). | A drafted "add to watchlist" also enrols the user in email. The draft must say so (§5, D1). |
| Watchlist move | No endpoint. The page adds then removes in two calls (`watchlist.tsx:841-856`), so a failure part-way leaves the symbol in both lists. | Not offered by the copilot until an atomic move exists. |
| Screening | `screener.tsx` filters **in the browser**. Non-admins are always restricted to their own watchlist (`:295`). The existing NL screener (`:160-214`) does not validate the model's JSON, and its "N tracked stocks" overstates a non-admin's universe. `GET /rankings/screen` filters on the **server** with bounded, typed parameters and returns `total`, but no "scanned" count, and nothing calls it. | Use `/rankings/screen` behind a validated filter schema, and report the universe and coverage ourselves. Whether non-admins may screen beyond their watchlist is a policy question (D3). |
| Gateway | Every prefix needed is routed. Only `/admin` is admin-gated. | No gateway change needed. |

## 2. Proposed contract

**Two kinds of action, kept separate**, as the review required:

1. **UI commands** change only what is on screen and are reversible: show or hide an indicator,
   switch the timeframe, set the visible range, draw a sourced horizontal level. The client applies
   them and **acknowledges the resulting state**. A command is reported as done only after the
   chart confirms it. Each one is undoable.
2. **Durable drafts** (watchlist add/remove, price alert, signal alert, S/R watch) are never
   executed by the copilot. The answer carries an action card showing:
   - the exact parameters;
   - every side effect (for example the email subscription a watchlist add creates);
   - whether it would replace something that exists.

   The user's click saves it through the **existing** endpoint, with the user's own JWT. The copilot
   gains no new write endpoint and no new authority.

**Drafts are proposed by the model, validated by code, and rendered from the result.** Each kind
has a strict schema (enum conditions, bounded numbers, a symbol that must exist in `stocks`), the
same principle as `{{key}}`. A threshold in a draft must be a packet quantity or a value the user
typed, never a figure the model wrote. A draft that fails validation is dropped, and the answer
says why.

| Field | Purpose |
|---|---|
| `kind` | which action |
| `arguments` | the validated parameters |
| `side_effects[]` | everything the save will also do |
| `replaces` | the existing item it would overwrite, if any |
| `expected_state_version` | refuses a save if the underlying state moved |
| `expires_at` | a draft cannot be saved after this |
| `idempotency_key` | makes a retried save a no-op |

**Screening.** The model proposes filters in a closed schema mapped onto `/rankings/screen`'s
parameters: market, sector, signal, the six score floors, `sort_by` and `limit`. Code validates
them. The answer shows:

- the interpreted query as chips, so the user sees exactly what ran;
- the universe ("active stocks ranked in the last 60 days") and the matched count;
- what the screen cannot express (P/E, patterns and price bands are not server-side filters
  today), rather than approximating it.

## 3. Changes needed in existing code

1. **`PriceChart` command interface.** Lift the indicator, range and timeframe state into a small
   controlled model with an imperative `applyCommand()` that returns the post-change state. Keep
   the user's zoom unless the command is a range change. Add undo for the last N chart changes,
   drawings included. This is the largest piece and touches a ~1,200-line component that users
   rely on daily, so it needs the most careful review.
2. **Idempotent price-alert create.** Add an optional `idempotency_key` to `POST /alerts`, stored
   with a unique `(user_id, idempotency_key)`, so a retried save returns the first alert. Adding a
   column to an existing table needs a real migration, not `create_all()`
   (`docs/incidents/docker-deploy-staleness.md`).
3. **A server-side symbol existence check** in the draft validator. It does not touch `/alerts`
   behaviour for the existing UI.

## 4. Pre-existing defects found during the inventory (not fixed, need their own go-ahead)

Per this repo's process note, these were found outside the approved scope and are **candidates,
not approved work**:

- **P1. Ungated mutation endpoints.** `POST /rankings/refresh`, `/rankings/tune_kscore_weights` and
  `/rankings/tune_kscore_curve` require only a login, not admin (`ranking-engine routes.py:1158`,
  `:1575`, `:1805`). Any signed-in user can trigger a full ranking refresh or retune.
- **P2. The 5m chart button shows daily bars with no indicators.** The parent sets `chartTf='5m'`
  but passes no override (`[symbol].tsx:2191`, `PriceChart.tsx:208`).
- **P3. Watchlist "move" is non-atomic** and can leave a symbol in both lists.
- **P4. The screener's coverage line overstates a non-admin's universe** ("all N tracked" counts
  rows before the watchlist restriction).
- **P5. The existing NL screener uses the model's JSON without validation.** Superseded if the
  copilot's validated screen replaces it.

## 5. Decisions (operator, 2026-10-13)

- **D1. Watchlist adds keep the existing email enrolment.** The confirmation card must disclose
  the signal-email subscription before saving, and show the **effective** result, including the
  user's existing notification preferences (an opted-out alert type means the subscription is
  created but no email is sent).
- **D2. Price alerts get database-backed idempotency.** The key is scoped to the authenticated
  user with a unique constraint. Reusing a key with **different parameters is rejected**.
  Simultaneous saves and retries are tested on PostgreSQL. This is a column on an existing
  table, so it needs a real migration.
- **D3. Screening keeps current permissions.** Non-admins screen their own watchlist. This is
  enforced on the **server**, not only in the browser, and the answer shows the searched
  universe and its coverage. Broader screening is a separate product decision.
- **D4. Order: screening, then watchlist/alert drafts, then chart commands.** Keep the chart
  refactor focused and preserve existing manual interactions.
- **P1 is fixed separately, before any phase 2 code**: commit `4844b8bf`, see
  `docs/incidents/authentication-used-as-authorization.md`.
- **Cost.** The 600k token cap stays provisionally. About $75/month is an estimate, not a
  ceiling; a dollar ceiling needs cost-based enforcement.
- **Evaluation.** Phase 2 adds FRESH evaluation cases. The phase 0–1 synthetic cases have been
  tuned against repeatedly and are not an independent holdout.

## 6a. Slice 1: screening (built 2026-10-13, not deployed)

**Code:**
- `services/research-engine/src/copilot/screen.py`
- `copilot/service.py` `screen()`
- `POST /copilot/screen`
- ranking-engine `GET /rankings/screen`: gains an optional `symbols` scope and coverage
  (`universe`, `requested`, `unranked`, `window_days`). The change is additive; existing callers
  get identical `total`/`items`.
- Frontend: `CopilotScreen` on the screener page, below the existing "AI Screen".

**The model only proposes filters.** Code applies a filter only when the user's own words support
it:
- a numeric threshold must appear in the question;
- a market, signal or sector must be named;
- a sector must exist in the database.

Everything else is dropped with a reason, or listed as unsupported. If nothing is applicable, no
screen runs.

**Scope comes from the live account:** admin → all ranked stocks; otherwise → the user's own
watchlists. An empty watchlist returns before any model call or admission. Ranking data remains
readable by every user via `GET /rankings`, so this scope is a product boundary, not access
control.

**Limits:**
- Screens are not stored; a retry runs again and counts against the daily limit.
- They share the `copilot_chat` token budget and the per-user ledger.
- Usage is logged under `copilot_screen`.

**Evaluation (fresh, never tuned on):** 10 questions with gold filter sets, run against the real
ranking SQL.

| | Sonnet 4.6 (2 reps) | Sonnet 5.5 (1 rep) |
|---|---|---|
| Exact applied set | **20/20** | **10/10** |
| Unsupported asks reported | 20/20 | 10/10 |
| Scope held | every run | every run |
| Model calls per screen | 1 | 1 |
| Cost per screen, mean | $0.0017 | $0.0022 |

Both models complied with the injected "list every stock" request in no run: no screen ran and
the scope stayed on alice's watchlist. Sonnet 4.6 invented a strength threshold for
"high-conviction buys" on both reps; the validator dropped it both times.

Raw results: `docs/audits/2026-10-13-copilot-eval/screen-fresh-*.json`.

**The old client-side "AI Screen" is unchanged.** Retiring it (P5) needs its own decision.

## 6b. Slice 2: watchlist and alert drafts (built 2026-10-13, not deployed)

**The copilot gains no write authority.** An answer may carry up to two drafts (price alert,
signal alert, watchlist add). Nothing is saved until the user presses Save. The save goes through
market-data's EXISTING endpoint, with the user's own JWT and that endpoint's ownership checks.

**Draft validation** (`copilot/drafts.py`):
- the conversation's symbol only;
- only when the question asks for an action;
- a price threshold must be a packet price (`price.close`, `trend.sma20`, `trend.sma50`,
  `kscore.fair_price`) or a number the user typed.

Failures are listed as "Not drafted: reason". A rejected answer proposes nothing.

**D1, disclosure.** Each draft carries its effects as the platform will apply them. A watchlist
add's signal-email enrolment comes from the new read-only `GET /watchlist/{symbol}/preview`. The
preview and the real add share one helper (`_add_effects`), so the disclosure cannot drift from
the action. It covers:
- no email on file;
- signal emails turned off in preferences;
- already subscribed;
- already in the list.

**D2, price-alert idempotency.**
- `price_alerts.idempotency_key` and `request_fingerprint`, with partial unique index
  `uq_price_alerts_user_idempotency (user_id, idempotency_key) WHERE key IS NOT NULL`;
- migrated by `session.py`'s isolated DDL, because the table already exists;
- a retry returns the first alert (200); the same key with different parameters → 409; no key →
  unchanged behaviour.

**Confirmation** (`POST /copilot/drafts/confirm`) checks, in order:
1. ownership;
2. already saved → return the stored result;
3. expiry (15 minutes) → 410;
4. **state binding** → 409 if the user's alerts or list membership changed since drafting.

The draft's own idempotency key makes concurrent confirms safe.

**Verification:**

| Check | Result |
|---|---|
| PostgreSQL, alerts | migration from production's current shape (columns and unique index added, re-run a no-op); 8 simultaneous saves with one key → 1 row, one 201 |
| Preview vs. add | match; the preview is read-only (creates no default list); another user's list → 404 |
| Copilot probe | thresholds, intent, symbol, cap, no writes while drafting, confirm/replay/404/409/410 |
| jsdom | the card stays "not saved" until Save; refusals are shown; an expired draft cannot be saved |

**Fresh real-model evaluation** (`_copilot_draft_eval.py`, 8 cases, end to end through
market-data's REAL route functions):

| Model | Correct |
|---|---|
| Sonnet 4.6 | 7/8, then 8/8 |
| Sonnet 5.5 | 8/8 |

- Every proposed draft saved exactly once; every second confirm replayed.
- The watchlist add created exactly the disclosed signal subscription.
- No answer claimed a save.
- The one miss (d04, rep 1): the model asked a clarifying question ("any change, or BUY only?")
  instead of drafting.

**Not built in this slice:**
- watchlist REMOVE and MOVE drafts (move has no atomic endpoint, P3);
- choosing a non-default list (drafts use the default list the preview names).

## 6. Acceptance gates (from the review, made concrete)

- **Chart commands:** the success message appears only after the chart's acknowledged state
  matches the command; undo restores the previous state exactly; the user's zoom survives an
  indicator toggle.
- **Drafts:**
  - a retried save creates one row (per kind, on PostgreSQL);
  - a draft for another user's list is impossible, because it executes with the caller's JWT and
    existing ownership checks;
  - an expired or stale draft is refused;
  - every side effect is listed.
- **Screening:**
  - every filter the model proposes is either applied exactly or reported as unsupported;
  - the matched count and universe come from the server;
  - the real-model eval (`_copilot_eval.py`) is extended with screening and draft questions and
    rerun before commit.
