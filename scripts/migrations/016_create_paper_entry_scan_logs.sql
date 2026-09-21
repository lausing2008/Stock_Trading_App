-- Migration: create paper_entry_scan_logs (AUD-PTH08-PERSISTENTGATELOG)
-- Run once: psql -U stockai -d stockai -f 016_create_paper_entry_scan_logs.sql
--
-- A durable counterpart to the Redis-only `paper:gate_block:{id}` / `paper:no_entry_summary:{id}`
-- keys (4-hour TTL). Both an independent audit (PT-H08, docs/audits/2026-09-19-paper-trading-
-- horizon-threshold-audit.md) and this project's own live investigation of a portfolio that had
-- gone 16+ days without a trade hit the same wall: by the time anyone actually asked "why isn't
-- this trading," the Redis keys that recorded the real per-scan rejection reason had already
-- expired. See PaperEntryScanLog's own docstring (shared/db/models.py) for the full rationale.
--
-- One row per (portfolio, scan cycle) that resulted in a portfolio-level gate block OR a
-- non-empty per-candidate skip tally — matching the existing Redis writes' own "only write when
-- there's something to explain" convention. Purely additive: no existing table, column, or
-- index is touched, and nothing currently queries this table until the scan engine is wired to
-- write to it and a reader is built.
CREATE TABLE IF NOT EXISTS paper_entry_scan_logs (
    id                    BIGSERIAL PRIMARY KEY,
    portfolio_id          BIGINT NOT NULL REFERENCES paper_portfolios(id) ON DELETE CASCADE,
    scanned_at            TIMESTAMP NOT NULL DEFAULT now(),

    portfolio_gate        VARCHAR(64),
    portfolio_gate_reason VARCHAR(512),

    candidates_seen       INTEGER,
    skip_tally            JSON
);

CREATE INDEX IF NOT EXISTS ix_paper_entry_scan_logs_portfolio_id ON paper_entry_scan_logs (portfolio_id);
CREATE INDEX IF NOT EXISTS ix_paper_entry_scan_logs_scanned_at ON paper_entry_scan_logs (scanned_at);
CREATE INDEX IF NOT EXISTS ix_paper_entry_scan_logs_portfolio_time
    ON paper_entry_scan_logs (portfolio_id, scanned_at);
