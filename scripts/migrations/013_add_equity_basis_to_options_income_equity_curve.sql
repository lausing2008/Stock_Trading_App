-- Migration: add equity_basis to options_income_equity_curve (AUD-A19-EQUITYMIXEDBASIS)
-- Run once: psql -U stockai -d stockai -f 013_add_equity_basis_to_options_income_equity_curve.sql
--
-- The options income equity curve silently mixes TWO definitions of "equity":
--
--   before 2026-09-17 : equity = cash + collateral
--   from   2026-09-17 : equity = cash + collateral - short option liability   (AUD-T400)
--
-- Both are stored in the same `equity` column with nothing distinguishing them, so any return
-- or drawdown spanning that boundary measures a CHANGE OF DEFINITION as though it were a change
-- in the market. Live example at the time of this migration, the only two rows that existed:
--
--   2026-09-16 : 178,567 + 73,100            = 251,667   implied liability EXACTLY 0, with
--                                                        THREE open positions — a pre-fix row
--   2026-09-17 :  66,467 + 186,600 - 3,087   = 249,980   post-fix
--
-- The apparent -1,687 move between them is not a loss; it is mostly the liability term
-- appearing for the first time.
--
-- BACKFILL IS DERIVED FROM EACH ROW'S OWN ARITHMETIC, NOT FROM A CUTOFF DATE. A date-based
-- backfill would be a guess that silently mislabels any row written by an older deploy still
-- running past the cutoff; the arithmetic is self-evident and self-correcting.
--
--   * open_positions_count = 0 -> the two definitions are IDENTICAL (no short position exists,
--     so the liability term is zero either way). Such a row is unambiguous and is labelled with
--     the current basis, which is true of it.
--   * equity == cash + collateral with positions OPEN -> no liability was deducted -> pre-fix.
--   * otherwise -> the liability term is present -> current basis.
--
-- Additive and nullable: no existing reader breaks, and a row that somehow matches neither
-- branch stays NULL (= "unknown basis") rather than being assigned a comfortable default.
ALTER TABLE options_income_equity_curve
  ADD COLUMN IF NOT EXISTS equity_basis VARCHAR(48);

UPDATE options_income_equity_curve
SET equity_basis = CASE
    WHEN open_positions_count = 0
      THEN 'cash_collateral_less_liability'
    WHEN ABS(equity - (cash + collateral_committed)) < 0.005
      THEN 'cash_collateral'
    ELSE 'cash_collateral_less_liability'
  END
WHERE equity_basis IS NULL;
