-- Migration: create signal_outcome_horizons (T410-AUD-C02-C03)
-- Run once: psql -U stockai -d stockai -f 014_create_signal_outcome_horizons.sql
--
-- One row per (signal_id, window_days), resolved INDEPENDENTLY of every other window on the
-- same signal and independently of signal_outcomes' own primary-window row. See
-- SignalOutcomeHorizon's own docstring (shared/db/models.py) for the full audit rationale:
-- 201 non-test references to signal_outcomes were checked before choosing an ADDITIVE table
-- over adding a pending state to signal_outcomes itself, specifically because every one of
-- those 201 references keys off is_correct/pct_return being non-NULL (the current maturity
-- signal) or looks up one exact signal_id — a pending row inserted into that table would have
-- been silently treated as "already evaluated" by outcomes.py's own dedup guard, permanently
-- skipping the signal's real primary-window resolution.
--
-- Purely additive and inert until a reader/writer is deployed for it: no existing table,
-- column, or index is touched, and nothing currently queries this table.
CREATE TABLE IF NOT EXISTS signal_outcome_horizons (
    id                BIGSERIAL PRIMARY KEY,
    signal_id         BIGINT NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    stock_id          BIGINT NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    symbol            VARCHAR(32) NOT NULL,
    horizon           VARCHAR(16) NOT NULL,   -- SignalHorizon enum: SHORT | SWING | LONG | GROWTH
    signal_direction  VARCHAR(8) NOT NULL,    -- BUY | SELL
    signal_date       DATE NOT NULL,

    window_days       INTEGER NOT NULL,
    horizon_unit      VARCHAR(16) NOT NULL DEFAULT 'calendar_days',
    is_primary_window BOOLEAN NOT NULL DEFAULT false,

    entry_date        DATE,
    entry_price       DOUBLE PRECISION,
    target_date       DATE NOT NULL,
    exit_date         DATE,
    exit_price        DOUBLE PRECISION,
    pct_return        DOUBLE PRECISION,
    is_correct        BOOLEAN,

    status            VARCHAR(16) NOT NULL DEFAULT 'pending',
    resolved_at       TIMESTAMP,
    created_at        TIMESTAMP NOT NULL DEFAULT now(),

    CONSTRAINT uq_signal_outcome_horizons_signal_window UNIQUE (signal_id, window_days)
);

CREATE INDEX IF NOT EXISTS ix_signal_outcome_horizons_signal_id ON signal_outcome_horizons (signal_id);
CREATE INDEX IF NOT EXISTS ix_signal_outcome_horizons_stock_id ON signal_outcome_horizons (stock_id);
CREATE INDEX IF NOT EXISTS ix_signal_outcome_horizons_symbol ON signal_outcome_horizons (symbol);
CREATE INDEX IF NOT EXISTS ix_signal_outcome_horizons_horizon ON signal_outcome_horizons (horizon);
CREATE INDEX IF NOT EXISTS ix_signal_outcome_horizons_signal_date ON signal_outcome_horizons (signal_date);
CREATE INDEX IF NOT EXISTS ix_signal_outcome_horizons_status_target
    ON signal_outcome_horizons (status, target_date);
CREATE INDEX IF NOT EXISTS ix_signal_outcome_horizons_lookup
    ON signal_outcome_horizons (horizon, signal_direction, window_days, status);
