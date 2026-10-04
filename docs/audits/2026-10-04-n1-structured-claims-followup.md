# N1 structured claims follow-up — 2026-10-04

Reviewed local HEAD `f99958ec`. The previous free-text API is disabled, quantities carry metric identity, comparison checks are pairwise, and dictionaries/lists are detached and frozen. These are substantive improvements. This review does not independently verify deployment or the full service suite.

Executed the real `narrate()` entry point against N1's fixture with a fully identified estimate. Three unsupported outputs are still accepted. [Offline reproduction](evidence/2026-10-04-n1-structured-claims-review.py) performs no model, network or database calls.

## SC-01 — P1: an estimate renders as a reported actual

`Claim(REPORTED_FIGURE, ('eps_expectation',))` produces:

> Reported eps $31.82 per share (non-GAAP adjusted, fiscal Q4 2026).

The fixture's actual is 33.42. `check_claim()` excludes guidance from reported figures, but does not exclude expectations. Thus a correctly identified number still acquires the wrong semantic role in the deterministic renderer.

Require appropriate quantity roles for every claim kind, not only units/basis/period. Add an explicit expectation renderer when needed. Validate roles and operand order for comparisons, and do not let metadata completeness certify an observed actual.

## SC-02 — P1: claim comments still introduce contradictory numbers

A legitimate reported-revenue claim with comment `Revenue was fifty billion dollars.` is accepted and renders:

> Reported revenue $54.23B (GAAP, fiscal Q4 2026). Revenue was fifty billion dollars.

`literal_numbers()` recognizes digits, not spelled quantities. More generally, arbitrary comments can assert unsupported facts without any numbers. Claim scope reduces one bypass but does not make the comment evidence-grounded.

For a deterministic publication guarantee, use approved interpretation/condition IDs with validated arguments and server-rendered text. Alternatively, treat arbitrary comments as fallible model prose requiring separate evaluation/review, not as factual clauses made safe by typed quantities. Adding number words to a deny-list alone does not solve the semantic boundary.

## SC-03 — P1: a resolvable evidence ID is not proof of attribution

An attributed interpretation with `attributed_to='Chief executive'`, `source_evidence_id='issuer_document:1'`, and `comment='Demand caused the rally.'` is accepted. That fixture's evidence contains only a source marker, no passage and no speaker attribution.

Require a specific source statement, speaker identity and passage locator. Rendering “X said Y” is itself a factual claim. Bind it to a recorded statement; it cannot be authorized merely because a document exists. For paraphrases, preserve the exact evidence and acknowledge semantic-validation limits. Do not waive causal restrictions based on an invented attribution.

## Integrity scope qualification

The supplied set-leaf test establishes detachment, not deep immutability: `deep_freeze()` returns a set unchanged after the deepcopy. Hash verification is a useful additional control, but “read-only at every level” is too broad for unsupported input types. Prefer rejecting non-JSON leaves and non-finite numbers, or explicitly canonicalize and freeze them. `json.dumps(default=str)` is not a substitute for a strict packet schema.

## Recommendation

Keep model publication off. Layer 1 interpretation development may proceed using deterministic findings and known-safe renderers while these claim boundaries are fixed. Do not mark the full publication gate sound based solely on the previous witness becoming incompatible with the removed API. Retain tests that exercise the replacement interface and require both false claims to fail and supported alternatives to render correctly.

No runtime code, production state, flags, notifications or CLAUDE.md were changed by this review.
