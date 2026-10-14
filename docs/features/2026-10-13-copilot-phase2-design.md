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
