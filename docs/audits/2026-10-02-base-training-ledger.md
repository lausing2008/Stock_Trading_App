# The base training ledger

Date: October 2, 2026. Built, tested and deployed. **No model was retrained, no threshold
tuned, no history window or split proportion changed, and no flag flipped.** Every number
in this document is a property of the instrumentation, not a production measurement — the
first real measurement arrives with the next scheduled retrain.

## What this answers that the previous ledger could not

`AttritionLedger` (M13, 2026-10-01) measures the outcome-augmentation branch: how many
closed live signal outcomes were loaded, and where they went. It does that well. It cannot
say why a fit reported eleven rows, because the rows it tracks were never in the main
lineage.

The base ledger measures the main lineage, as nine stages:

| Stage | What it loses |
|---|---|
| `loaded_bars` | nothing — what the database held at fit time |
| `completed_unique_bars` | bars dated today, which are partially observed |
| `required_features` | warm-up and any row missing a core daily feature |
| `available_labels` | the last `horizon` bars, which have no forward return yet |
| `dead_zone_selection` | rows whose forward move was too small to label confidently |
| `deduplication` | rows superseded by a real live-trade label for the same date |
| `split_allocation` | nothing — it partitions 70/10/10/10 |
| `embargo` | the gap taken after each boundary so labels do not straddle it |
| `threshold_report_sets` | the half of the test slice consumed selecting the threshold |

Each stage records rows in and out, its exclusion reasons, its date range, and class support
where it applies. The chain is checked end to end: a stage whose `rows_in` does not equal the
previous stage's `rows_out` is reported as a **chain break**, which is a different failure
from a stage that fails to reconcile internally.

## Three measurement rules the module enforces

**1. Disjoint counts reconcile; diagnostic counts do not.** A row can be missing a required
feature *and* have no forward label *and* sit inside the dead zone. Each stage therefore
carries two separate books. `dropped` is a first-match attribution: a row is counted under
the first criterion that rejected it, and the book must sum exactly to the stage's loss.
`diagnostics` counts every criterion that applied to a row, whether or not it was the reason
of record, and is never reconciled against anything. On a real 420-bar frame the diagnostic
total exceeds the actual loss — a ledger that reconciled against it would report a shortfall
nobody lost. Over-attribution is treated as a defect too: `unexplained` goes negative when
two buckets claim the same row, and `reconciles` is false either way.

**2. Weighted augmentation is not an observation count.** Outcome rows enter the final fit
at double weight. Twelve such rows are twelve observations carrying twenty-four units of
weight — not twenty-four observations, and not part of any stage count. They are recorded in
their own block, with a field named `augmentation_weight_total_is_not_a_sample_size` so the
distinction survives being read by someone who did not write it.

**3. An absent number is `None`, not `0`.** `rows_out=None` means NOT MEASURED and requires
a reason; the ledger is then `complete=False`. A stage that measured zero records `0`. The
module raises rather than let those collapse.

## The two acceptance requirements

**Instrumentation leaves training behaviour unchanged.** `build_features` takes a `trace`
out-parameter. The three selection criteria were lifted out of the mask expression and given
names, so the trace reports on the *same* boolean series the mask is built from rather than
recomputing its own version — a trace that recomputes a filter can disagree with it. The
test calls the real `build_features` twice on the same frame, with and without a trace, and
asserts the returned features, labels, forward returns and surviving row *identities* are
identical. A source-level test backs it up for every fit rather than the one that ran: no
assignment to a row-selecting, weight-building or boundary-setting variable in `train_model`
may read the ledger or the trace.

Two sabotages confirm the test bites: flipping one actually-selected row, and reversing the
label order, both fail it. A third sabotage — forcing the first five mask positions to
`False` — *survived*, and that is worth recording: those rows are warm-up rows the mask had
already rejected, so the mutation changed nothing. The sabotage was a no-op, not a gap in
the test.

**The ledger explains empty and aborted fits.** A ledger is written for every ending, not
only the ones that save an artifact. Each `skipped` return records its stage and reason; an
exception — including the single-class `raise` and anything a model library throws mid-fit —
is caught at the boundary, which marks the ledger `aborted` with the exception and the stage
reached, emits it, and re-raises. A test walks `train_model`'s AST and fails if any
no-model exit lacks an outcome.

The ledger stored *inside* an artifact is a snapshot taken before the save, so it cannot
know its own outcome; it reads `reached_artifact_write` rather than `in_progress`, which
would describe the fit as unfinished when what is unfinished is the ledger. The
authoritative outcome is the logged one.

## The eligible-opportunity cohort

Training evaluates itself on the rows it selected: moves large enough to clear the dead zone,
which is fitted to half the symbol's own expected move. Serving is asked about every bar.
An accuracy measured only over clear moves cannot speak for that population, and the gap is
not a rounding error — the excluded rows are the ambiguous majority by design.

The cohort preserves, for the test window, every row with complete features and a resolved
forward label, **including the small moves training dropped**, each with its date, forward
return, direction and whether training selected it. It is recorded, never trained on: no
label, weight or selection changes. It exists so that an evaluation over the full eligible
population becomes possible without a retrain. When the window or the row dates cannot be
determined it reports `rows: None` with a reason and renders no count.

## Two conclusions narrowed, as instructed

**"Roughly half" belongs to the holdout branch only.** The measured MU GROWTH 27→14 and MU
LONG 21→11 describe fits that took the threshold/report split. On the `in_sample_fallback`
branch the metrics are computed over the whole test slice, so `n_test` *is* their denominator
and nothing is understated. Nor does the claim transfer to artifacts written by earlier
versions of `trainer.py`, whose split semantics must be read off the code version that
produced them. `n_metric_rows` is the denominator to use, and it is recorded either way.

**755 bars does not rule out insufficient history.** Both symbols currently store 755 daily
bars. That establishes that filtering and allocation also matter; it does not establish how
much usable history existed at each fit. The query returns what the database holds *today*,
training never fetches missing vendor history, and nothing stored says how many of those bars
existed, or were usable, at the time of any historical fit. The base ledger's `loaded_bars`
stage records this per fit going forward; it cannot be reconstructed backwards.

## What this does not establish

- **No fit has run with it.** The wiring is tested; the measurement is not yet made. The
  first instrumented ledgers appear with the next scheduled retrain.
- **Rows are not independent observations.** The ledger counts rows. Adjacent daily bars
  share overlapping feature windows and overlapping forward-return windows, so a larger row
  count is not proportionally more evidence. Unique session and event counts remain
  unmeasured, and this ledger does not supply them.
- **Purge, calibration and promotion exclusions still have no ledger** — the same gap M13
  recorded on 2026-10-01.
- **Historical artifacts cannot be back-filled.** Their per-stage losses are not
  reconstructable from stored metadata.
- **Nothing here is a reason to loosen suppression.** Per the instruction, the ledger is to
  be reviewed after the first instrumented fit, *before* any change to history windows or
  split proportions.

## A red check that had already shipped

Running the full suite for this change surfaced that `prod` was already failing the T401
ratchet — the guard that forbids new test assertions pinning a NUMBER inside a source-text
match. The previous commit, `13d89f1b` (M13-TRACE), had pushed it from 180 to 182, and I
had reported that work as green on the strength of the ml-prediction suite alone rather than
`make test`. A service-scoped run cannot see a repo-wide guard that lives in another service.

Both of those assertions, and the one this change would have added, are now value
assertions parsed from the AST: the CV-AUC suppression floor and the near-random warning
threshold are read as numbers and asserted separately (pooling them would let either move to
the other's value), the reporting floor is read from its assignment, and the augmentation
weight multiple is read from the call's keyword. The count is back at 180.

One of the three was a self-match: a docstring quoting the anti-pattern as an example is
indistinguishable from a real offender to a scanner. That is the second time this exact
shape has caught me — the first was a credential test that matched its own explanation of
why masking is avoided.

## Where it lives

- `shared/metrics/base_ledger.py` — the ledger, the proxy, the boundary and the cohort
  helper. Pure: no I/O, no pandas, no model imports, importable by an audit probe without
  the training stack.
- `services/ml-prediction/src/features/builder.py` — the `trace` out-parameter.
- `services/ml-prediction/src/training/trainer.py` — the stage wiring, and
  `metrics.base_ledger` on each artifact.
- `services/ml-prediction/tests/test_m13_base_training_ledger.py` — 31 tests.
- `docs/audits/evidence/2026-10-02-base-ledger-selection-probe.py` — a READ-ONLY probe that
  runs the real selection stages against real production prices. It trains nothing and
  writes nothing, and it exercises only the three selection stages and the cohort; the
  later stages belong to a fit.
