# Copilot: remediation of the independent re-review (2026-10-14)

This responds to [the re-review](2026-10-14-copilot-remediation-independent-rereview.md). Both
parser defects are fixed. The Unusual Whales repair is a separate commit (`ae2765f3`).
**Implementer-verified; not re-reviewed. Not deployed.**

## C1 (P1): a number is read whole, and a window is never a price

`copilot/intent.py`, `parse_alert`:

- **Named sources first.** "50-day average" (with or without "its") is matched before any number,
  so its window can no longer become "$5".
- **Complete amounts only.** The number token must be complete: grouped thousands ("1,000") are
  supported explicitly, decimals are kept, and sentence punctuation still ends a number
  ("below 100.", "above 70, top 5").
- **Refused, with a reason:** an incomplete number ("1,00"), a number with letters ("10k"), and a
  number naming a window ("below 50-day" with no average named).
- **A percentage anywhere refuses price alerts outright.** The old code only applied the refusal
  when no target parsed, so a partial target could slip past it.

Tests: 8 wrong proposals refused, including all three of the reviewer's cases. 5 correct
proposals accepted: the 50-day average with and without "its", "1000" and "1,000" for "below
1,000", and "12.5".

## C2 (P2): negation, OR, and percentage scores

`copilot/intent.py`, `parse_screen`, and `screen.validate`:

- **Negated comparisons are refused.** "not above 70" and "isn't below 40" are explained and
  never inverted or dropped silently. The fixed comparators "no more than" and "no less than"
  still work.
- **Any "or" / "either" applies nothing.** Every proposed filter is dropped with the reason that
  the screen can only AND, and the answer asks for one condition at a time.
- **A score given as a percentage** ("above 70%") is refused with a clarification: scores are
  0–100 points.

Tests: 5 refusal cases, each required to carry an explanation, plus "no more than 70" →
`max_score=70`.

## Ranking authorization tests that timed out in the reviewer's environment

The stall was in AnyIO's worker thread, which FastAPI uses for sync dependencies such as
`require_model_admin`. The authorization proof no longer depends on that thread:

- **Wiring:** the real route objects are introspected, and each route's dependency tree must
  contain the real `require_model_admin` and not `get_current_username`.
- **Decisions:** the real dependency is called directly for signed out, ordinary user, demoted
  admin, disabled admin, unscoped service, active admin and scheduler.

The HTTP tests remain. They first probe the worker thread (bounded at 5 s) and report **skipped**,
with the reason, where it does not run. Simulated here: 11 passed, 18 skipped, none hung.
Removing the gate turns the thread-free wiring test red on its own.

## Validation

| Check | Result |
|---|---|
| Copilot suite | 45 passed |
| Ranking suite | 201 passed |
| `make test`, all 12 services | passed |
| Reviewer probe | every wrong proposal dropped; the correct average proposal accepted |
| Real model, same fresh cases | screen 10/10, drafts 7/8 (the recurring d04: the model answers without drafting) |
