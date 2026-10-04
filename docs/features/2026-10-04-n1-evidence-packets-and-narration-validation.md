# N1 — frozen evidence packets, structured eligibility, and offline narration validation

Date: October 4, 2026. **Narration remains disabled.** Nothing in this slice calls a model, and
no flag is activated by building it.

## The inversion this makes

The usual arrangement asks a model in a prompt to be careful, then checks its output for having
not been. That is a hope, not a rule. Here, **what may be claimed is computed from the evidence
before any text exists** — a comparison is eligible only where both sides sit on an established
basis, a guidance *change* only where a comparable prior forecast exists, a causal claim never.
A draft is then graded against the same packet, and a failing draft is replaced by the
deterministic text rather than repaired, because a sentence edited to pass a check is no longer
the output that was checked.

## 1. Immutable packets (`shared/intelligence/evidence_packet.py`)

A packet is pinned to `report_id`, `report_version`, `cutoff_at`, `contract_version`,
`policy_version`, the fields, and the evidence records needed to resolve its own citations
offline. It is content-addressed (`sha256-packet:`).

**Why not just pass the report.** A report is live: regenerate it and fields move. A narration
written against one and stored beside another is unauditable — the sentence survives, the
numbers it came from do not, and afterwards nobody can tell whether the narrator was wrong or
the inputs changed underneath it.

The hash covers **eligibility as well as fields**, so the rules cannot change underneath a
narrative that was accepted under them. A revised report yields a different packet and the
earlier packet still validates its own narrative.

## 2. Structured eligibility

| Claim | MU packet #12 | Why |
|---|---|---|
| `REPORTED_FIGURE` | allowed | figures present with units and a stated basis |
| `PERIOD_IDENTITY` | allowed | source-confirmed from the issuer's release |
| `COMPARISON` | **refused** | both sides present, but the *estimate's* basis is not stored |
| `GUIDANCE_LEVEL` | allowed | the company's own current guidance |
| `GUIDANCE_CHANGE` | **refused** | no comparable prior forecast for the same period |
| `PRICE_REACTION` | allowed | a return that names the window it spans |
| `CAUSAL` | **never** | attributing a move needs a counterfactual this platform does not have |

Two distinctions the structure enforces that prose could not:

- **Two numbers are not a comparison.** A GAAP actual against an adjusted estimate is a
  difference. Supplying the estimate's basis flips `COMPARISON` to allowed; nothing else does.
- **A guidance LEVEL is not a guidance CHANGE.** They have different inputs, so they are
  different claims with their own eligibility.

`CAUSAL` is refused permanently, not pending more data — no quantity of prices and figures
establishes that one caused the other.

## 3. Contradiction checks (`shared/intelligence/narration_validator.py`)

Seven checks. The ones worth naming:

- **Support and consistency are separate properties.** "EPS of 33.42 beat the 31.818 estimate"
  invents nothing — every figure is real — and is still refused, because the comparison is not
  eligible. A validator checking only for invented numbers passes this.
- **A conflict presented as settled.** Where the provider and issuer disagree, *both* values are
  in the packet, so a number check passes either. "Revenue was 54.23 billion, confirmed by all
  sources" is a false claim built entirely from true numbers. Quoting the provider's side
  without saying it is disputed is flagged separately.
- **A price move without its window.** A bare "3.03%" reads as the announcement's effect; it is
  a close-to-close return over a window that can include pre-announcement trading.
- **Contradicting the report's own verdict.** A draft claiming a raise is flagged both as
  ineligible and as contradicting the forward verdict that says the comparison is not
  established.

**Matching is numeric, not textual.** String comparison made "54.0" a different number from
"54" — a validator that rejects correct text is worse than none, because it gets switched off.
Values match against their legitimate scalings, so $54.23B correctly matches a stored
54230000000.

## 4. Historical wording and fallback

A packet built from a superseded or corrected report carries `historical.required_framing`: past
tense, as what was known at the cutoff. A draft saying "the latest results" against such a
packet is refused. A current packet carries no such framing.

On any violation the published text is the **deterministic** text, every violation is reported
(not just the first), and the draft is discarded.

## 5. Offline cases

21 tests, no model: missing evidence (narrows eligibility, still builds a packet), **stale**
(not treated as resolved — `STALE` is not `OK`), **conflicting** (settled-wording and
provider-side assertions both refused, an honest sentence accepted), and **revised** (a new
packet, with the old one still resolving its own narrative).

Sabotage-verified: allowing `CAUSAL`, disabling the comparison gate, treating an unknown
estimate basis as established, and returning the draft instead of the fallback each fail their
owning tests.

## Review of this slice: four P1 findings, all reproduced

`docs/audits/2026-10-04-n1-packet-validation-review.md` reviewed commit `aa6d19a9`. I re-ran
each finding against the real functions rather than accepting the report. **All four reproduce.**
The verdict that matters: N1 is an offline prototype and **is not a reliable publication gate**.
A finite set of hand-written drafts passing does not establish that arbitrary prose respects the
structured permissions — and my 21 tests were exactly such a finite set.

| # | Finding | Reproduced |
|---|---|---|
| N1-R01 | Numbers lose metric and unit identity. `_packet_values()` pools every number into one untyped set, so *"Revenue was $33.42 billion"* (the EPS figure) and *"Revenue was $54.23 million"* (wrong magnitude by 1000×) are both **accepted**. | yes, both |
| N1-R02 | Comparison eligibility checks presence, not comparability. A **GAAP** estimate basis authorises a comparison against the **non-GAAP adjusted** actual, and permission is report-wide rather than attached to the specific pair. | yes |
| N1-R03 | Enforcement is keyword-based and bypassable. *"Profits surpassed analyst forecasts"* evades the comparison list; a safe phrase **anywhere** in the draft disarms the causal guard for the whole text. | yes, both |
| N1-R04 | The packet is only shallowly frozen. `@dataclass(frozen=True)` blocks attribute assignment, not nested mutation: `packet.fields[...]['value']['value'] = 999` succeeds, `packet_hash` is unchanged, **and the original report payload is mutated too** because the dicts are shared. | yes, all three |

### What I got wrong, specifically

The numeric-matching change earlier in this slice fixed a real false-reject ("54.0" vs "54") and
in doing so made the opposite error worse: a bag of magnitudes with no metric, unit or period
attached cannot tell revenue from EPS. I described it as support checking; it is weaker than
that. And I called `@dataclass(frozen=True)` an immutable packet without testing nested
mutation — the one property the whole design rests on.

### The corrected direction

The review's remedy is right and is a different interface, not a patch: the narrator should emit
**typed, referenced claims** — metric, issuer, period, basis, units, evidence id — with factual
clauses rendered deterministically server-side, each claim's eligibility enforced pair-by-pair.
Free prose needs semantic review and stays fallible; a keyword validator must never be described
as proving semantic safety. The packet needs detaching and deep-freezing (or authoritative
serialized bytes with a verified digest), and the eligibility POLICY needs versioning, because
content hashing does not preserve the rules that gave the content its meaning.

**Status: all four CLOSED**, and the review's own witness script now prints `False` on every
line where it printed `True`: [recheck](../audits/evidence/2026-10-04-n1-remediation-recheck.py).
Narration still stays off — closing these makes the gate sound, not the narrator proven.

### What changed, per finding

**N1-R01 — the narrator no longer writes numbers at all.** `quantities.py` extracts every figure
once, with its metric, units, basis and period, keyed by a stable id. A claim NAMES a quantity
and the clause is rendered deterministically (`$54.23B (GAAP, fiscal Q4 2026)`). There is no
route by which the EPS value can be printed as revenue, because the name carries the measure —
and no route to a magnitude error, because the model never types the magnitude. A figure typed
into a comment is refused outright.

**N1-R02 — comparability is decided pair by pair.** `comparable(a, b)` requires the same metric,
the same accounting basis, the same period and a compatible unit family. A GAAP estimate against
a non-GAAP actual is refused by basis; an eligible EPS pair no longer authorises a revenue one.
The report-wide `accounting_basis.estimate_basis` check is gone.

**N1-R03 — the ungated prose path was removed, not extended.** `narration_validator.validate`
now raises `ImportError` explaining why. Grading undifferentiated text can only ask "does this
contain a forbidden phrase", and both bypasses are properties of that shape, not of the
particular phrases: "profits surpassed analyst forecasts" is not on any maintainable list, and
an attribution in one sentence cannot be scoped to another. A comment now belongs to a claim and
is checked against THAT claim's permissions, so neither bypass has anywhere to live. Causal
language is permitted only in an `ATTRIBUTED_INTERPRETATION` carrying a named source and a
resolvable evidence id.

**N1-R04 — detached, deep-frozen, and verified before use.** The payload is deep-copied, then
wrapped read-only at every level. `verify()` re-derives the hash, and `narrate()` refuses any
packet that fails it. The hash now covers the eligibility REASONS, the pairwise comparison
results, the quantity identities and an `ELIGIBILITY_POLICY_VERSION` — content hashing alone
does not preserve the policy that gave the content its meaning.

### Two things this round got wrong first

**Deep-freezing silently disabled downstream checks.** `MappingProxyType` is not a `dict`
subclass, so every `isinstance(x, dict)` on packet data became `False` and the checks behind
them stopped running. Found because an existing test failed for the wrong reason. All such sites
now test `collections.abc.Mapping`, with a regression that asserts the guarded checks fire.

**A sabotage check passed when it should not have.** Removing the `deepcopy` from `build_packet`
left all 42 tests green, because `deep_freeze` rebuilds dicts and lists and so detaches those
anyway — the copy only matters for a leaf type the freezer has no rule for. The detachment test
was therefore not load-bearing. It now uses a `set` leaf and fails when the copy is removed.

## Structured-claims follow-up: three more accepted outputs, and a test that proved the wrong thing

`docs/audits/2026-10-04-n1-structured-claims-followup.md` probed the *new* `narrate()` interface
and found three outputs it still accepted. All three reproduced; all three are closed. The
extended [recheck](../audits/evidence/2026-10-04-n1-remediation-recheck.py) now refuses all
seven probes across both rounds.

**F1 — an estimate published as a reported result.** `"Reported EPS $31.82"` was accepted using
the *expectation*, not the $33.42 actual. Identity and comparability were both satisfied: it is
correctly typed as EPS, correctly carries units, basis and period, and is entirely comparable.
What it is not is something the issuer reported. **A quantity's ROLE is a separate fact from
what it measures**, and roles are now declared per claim kind — `reported_figure` requires an
`actual`, `guidance_level` requires `guidance`, and each refusal names the role it found.

**F2 — a contradictory comment appended to a correct rendering.** The claim rendered
`$54.23B` correctly and then appended "Revenue was fifty billion dollars." No digits, so the
figure check could not see it. The remedy is not a better scanner: **arbitrary prose is outside
the deterministic guarantee**, so a factual claim now carries no free comment at all. Its
sentence is wholly ours. Commentary exists only as an attributed interpretation bound to a
recorded statement.

One consequence worth stating: the conflict warning used to depend on a narrator choosing not to
write "confirmed". With prose gone, a recorded source disagreement is now **rendered by us**,
appended to the figure deterministically — the guarantee moved to where it can be kept.

**F3 — an attribution with nothing behind it.** `"Chief executive: Demand caused the rally"` cited
an evidence id that resolved to a press release containing no statement and no speaker. A
resolvable citation is not a citation *of something said*. An attribution now requires the record
to carry both a speaker and a passage, the claimed speaker to match the recorded one, and the
attributed text to be contained in the recorded statement — a paraphrase that adds a claim is
refused, because otherwise it is the platform's own causal assertion wearing someone else's
name. Accepted attributions render as marked reported speech: *"X stated: '…' (reported
statement, not a finding of this report; source …)"*.

**F4 — the set-leaf test proved detachment, not immutability.** Correct, and a sharper reading
than mine. `deep_freeze` had no rule for `set`, so it passed the leaf through: detached from the
report by the copy, and still freely mutable inside the packet. Sets now freeze to `frozenset`,
and **any type with no freezing rule raises** rather than being passed through — a packet that
is immutable except where it is not is worse than one that is honestly neither.

Six guards, each sabotage-verified: role enforcement, the no-free-comment rule, speaker
matching, passage containment, set freezing, and the refusal of unsupported types.

## What this does NOT do

No model is called. No flag changes. The next milestone is accurate, useful **interpretation** of
a frozen packet — not successful model output, which this slice deliberately cannot produce.
