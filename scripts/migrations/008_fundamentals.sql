-- Migration 008: fundamentals table
-- Persists per-stock company fundamentals fetched from yfinance.
-- One row per (stock_id, as_of date) — upserted whenever /fundamentals is called.
-- Used as static ML features in the XGBoost pipeline.

CREATE TABLE IF NOT EXISTS fundamentals (
    id          BIGSERIAL PRIMARY KEY,
    stock_id    INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    as_of       DATE    NOT NULL,
    -- Valuation
    trailing_pe             DOUBLE PRECISION,
    forward_pe              DOUBLE PRECISION,
    price_to_book           DOUBLE PRECISION,
    -- Profitability
    gross_margin            DOUBLE PRECISION,
    profit_margin           DOUBLE PRECISION,
    return_on_equity        DOUBLE PRECISION,
    return_on_assets        DOUBLE PRECISION,
    -- Growth
    revenue_growth          DOUBLE PRECISION,
    earnings_growth         DOUBLE PRECISION,
    -- Cash flow / valuation
    free_cashflow           DOUBLE PRECISION,
    market_cap              BIGINT,
    -- Sentiment
    short_percent_of_float  DOUBLE PRECISION,
    short_ratio             DOUBLE PRECISION,
    -- Analyst consensus
    recommendation_mean     DOUBLE PRECISION,
    number_of_analysts      INTEGER,
    -- Housekeeping
    fetched_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fundamentals_stock_date UNIQUE (stock_id, as_of)
);

CREATE INDEX IF NOT EXISTS ix_fundamentals_stock_id ON fundamentals(stock_id);
CREATE INDEX IF NOT EXISTS ix_fundamentals_as_of    ON fundamentals(as_of);
