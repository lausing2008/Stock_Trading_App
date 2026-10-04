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

**Status: all four open.** Narration stays off, and this slice is not grounds for turning it on.

## What this does NOT do

No model is called. No flag changes. The next milestone is accurate, useful **interpretation** of
a frozen packet — not successful model output, which this slice deliberately cannot produce.
