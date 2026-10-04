# N1 packet and validation review

Reviewed commit `aa6d19a9`. **21 existing tests pass** using `python -m pytest -q --noconftest services/research-engine/tests/test_n1_packets.py`. Additional offline executions of the real functions reproduce the findings below. No model, database, network or production action was involved. Deployment claims are not independently verified.

Evidence: [reproduction script](evidence/2026-10-04-n1-validation-review.py). This prints defect witnesses, not a release gate. Date in the filename follows the N1 design document's date.

## Overall assessment

Computing eligibility before narration and retaining deterministic fallback are appropriate. N1 is an offline prototype; it is **not yet a reliable publication gate**. A passing finite collection of hand-written drafts does not establish that arbitrary prose respects the structured permissions.

## N1-R01 — P1: numbers lose metric and unit identity

`narration_validator._packet_values()` pools every numeric value, including parsed strings, into one set and adds magnitude scalings. `_numbers()` removes currency/percent notation and does not bind magnitude words to numbers.

Both drafts are accepted with the MU fixture:

- `Revenue was $33.42 billion.` — 33.42 belongs to EPS, not revenue.
- `Revenue was $54.23 million.` — the fixture's revenue is 54.23 **billion**.

**Solution:** structured claims reference a particular eligible metric, issuer, period, basis, units and evidence ID. Render numeric quantities server-side with explicit conversions and rounding policy. Do not use an untyped bag of numbers as proof. Test metric swaps, periods, currencies, percentages/fractions and magnitude changes; valid equivalent formatting must still pass.

## N1-R02 — P1: comparison eligibility checks presence, not comparability

`_basis_is_established()` merely checks that `estimate_basis` is nonempty and not `UNKNOWN`. Supplying a **GAAP** estimate basis allows `COMPARISON` against the fixture's **non-GAAP adjusted** actual. Units, fiscal period and matching basis are not established by that check. Permission is report-wide rather than attached to the specific comparison.

**Solution:** evaluate each actual/estimate pair independently. Require compatible metric, units/currency, target period, basis and required time provenance. Record whether it is a current-estimate difference or a frozen-consensus surprise. A valid EPS comparison cannot authorize a revenue comparison. Apply the same pair-specific logic to guidance levels and changes.

## N1-R03 — P1: free-text enforcement remains keyword-based and bypassable

These drafts are accepted:

- `Profits surpassed analyst forecasts.` — comparison is ineligible, but this phrasing misses the keyword list.
- `According to management, demand improved. AI caused the share rally because orders grew.` — the safe phrase anywhere in the draft bypasses the causal guard for the whole draft.

`validate()` also does not require an explicit claim type or evidence reference for each statement, and does not enforce every enumerated eligibility kind. The declared structured permissions therefore do not constrain every accepted sentence.

**Solution:** accept typed, referenced claims as the output interface, enforce each claim's eligibility, and render approved factual clauses deterministically. Source-attributed interpretations must be scoped to the specific claim and cited source passage; an attribution in another sentence cannot waive restrictions. Free prose needs additional semantic review and remains fallible. Do not describe a keyword validator as proving semantic safety; adding these few phrases alone will not solve the general problem.

## N1-R04 — P1: the packet is only shallowly frozen

`build_packet()` reuses mutable field/evidence dictionaries from the report. `@dataclass(frozen=True)` prevents attribute assignment, not nested mutation. Executing `packet.fields['eps_actual']['value']['value'] = 999` succeeds while `packet_hash` remains unchanged. The packet can therefore change under its recorded identity, potentially altering the original in-memory payload too.

The hash includes eligibility type and allowed boolean, but not its reasons/requirements or an explicit eligibility-rule implementation version. Content hashing is not a substitute for preserving the policy that gave the content its meaning.

**Solution:** detach and deeply freeze the canonical packet or retain immutable serialized bytes as the authoritative representation. Verify its digest before use. Persist/version the eligibility and validation policy, including material reasons and requirements, and reject tampered content. Test mutations through both the original report and nested packet fields/evidence.

## Next milestone

Keep model calls and publication off. Fix the four boundaries, add negative and positive acceptance cases, then proceed to offline model-generated drafts against the same frozen corpus. Evaluate unsupported claims, wrong units/periods, false acceptance and false rejection separately. Deterministic fallback remains independently reviewed and available; avoid optimizing rejection rate alone.

The new report interpretation design remains applicable: this validation work is infrastructure for evidence-supported assessment, not proof of a directional model or financial edge. No runtime code, flags, deployments or CLAUDE.md were changed by this review.
