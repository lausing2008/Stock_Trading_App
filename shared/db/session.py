"""DB session factory + init helper."""
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from common.config import get_settings

from .models import Base

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency — yields a DB session that's closed after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Idempotent metadata create — suitable for dev. Use Alembic in prod."""
    Base.metadata.create_all(bind=engine)
    _run_migrations()
    _apply_isolated_ddl()
    _apply_one_shot_migrations()
    _seed_admin()


_HK_ZH_NAMES = {
    "0700.HK": "騰訊控股", "0005.HK": "匯豐控股", "0939.HK": "建設銀行",
    "1299.HK": "友邦保險", "9988.HK": "阿里巴巴", "3690.HK": "美團",
    "0388.HK": "香港交易所", "1810.HK": "小米集團",
    "0981.HK": "中芯國際", "9961.HK": "攜程集團",
    "6082.HK": "壁仞科技", "6613.HK": "藍思科技",
}


def _run_migrations() -> None:  # noqa: C901
    with engine.begin() as conn:
        # Add Chinese name column and backfill known HK stocks
        conn.execute(text(
            "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS name_zh VARCHAR(256)"
        ))
        for sym, zh in _HK_ZH_NAMES.items():
            conn.execute(text(
                "UPDATE stocks SET name_zh = :zh WHERE symbol = :sym AND name_zh IS NULL"
            ), {"zh": zh, "sym": sym})
        # Assign orphaned strategies (owner='system') to the admin user
        conn.execute(text(
            "UPDATE strategies SET owner = 'lausing' WHERE owner = 'system'"
        ))
        # Add user_id column to watchlist_items if it doesn't exist yet
        conn.execute(text("""
            ALTER TABLE watchlist_items
            ADD COLUMN IF NOT EXISTS user_id INTEGER
            REFERENCES users(id) ON DELETE CASCADE
        """))
        # Drop the old per-stock unique constraint (may not exist on fresh installs)
        conn.execute(text("""
            ALTER TABLE watchlist_items
            DROP CONSTRAINT IF EXISTS watchlist_items_stock_id_key
        """))
        # ── Named watchlists ───────────────────────────────────────────────
        # Create watchlists table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS watchlists (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name       VARCHAR(128) NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT now(),
                UNIQUE(user_id, name)
            )
        """))
        # Add watchlist_id FK to watchlist_items
        conn.execute(text("""
            ALTER TABLE watchlist_items
            ADD COLUMN IF NOT EXISTS watchlist_id INTEGER
            REFERENCES watchlists(id) ON DELETE CASCADE
        """))
        # Create default "My Watchlist" for every user that has items
        conn.execute(text("""
            INSERT INTO watchlists (user_id, name)
            SELECT DISTINCT user_id, 'My Watchlist'
            FROM watchlist_items
            WHERE user_id IS NOT NULL
            ON CONFLICT (user_id, name) DO NOTHING
        """))
        # Assign orphaned items to their owner's default watchlist
        conn.execute(text("""
            UPDATE watchlist_items wi
            SET watchlist_id = w.id
            FROM watchlists w
            WHERE w.user_id = wi.user_id
              AND w.name    = 'My Watchlist'
              AND wi.watchlist_id IS NULL
        """))
        # Partial unique index: one stock per watchlist
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_uq_wl_item
            ON watchlist_items (watchlist_id, stock_id)
            WHERE watchlist_id IS NOT NULL
        """))
        # Drop the old per-user constraint that blocks multi-list membership
        conn.execute(text("""
            ALTER TABLE watchlist_items
            DROP CONSTRAINT IF EXISTS uq_watchlist_user_stock
        """))
        # ── User email ─────────────────────────────────────────────────────────
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(256)"
        ))
        # ── Price alerts ───────────────────────────────────────────────────────
        # alertcondition enum is created by Base.metadata.create_all() from models.py.
        # We only add values here for existing AWS DBs that were created before new conditions were added.
        for _val in ('CROSS_ABOVE_EMA', 'CROSS_BELOW_EMA', 'NEW_52WK_HIGH', 'NEW_52WK_LOW', 'GOLDEN_CROSS', 'DEATH_CROSS'):
            conn.execute(text(f"ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS '{_val}'"))
        # Pattern conditions added after initial DB creation (lowercase, matching Python enum values)
        for _val in ('macd_bullish_cross', 'rsi_oversold_bounce', 'double_bottom', 'breakout'):
            conn.execute(text(f"ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS '{_val}'"))
        # SA-13: add GROWTH horizon to the signalhorizon enum (idempotent on existing DBs)
        conn.execute(text("ALTER TYPE signalhorizon ADD VALUE IF NOT EXISTS 'GROWTH'"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS price_alerts (
                id           SERIAL PRIMARY KEY,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                symbol       VARCHAR(32) NOT NULL,
                condition    alertcondition NOT NULL,
                threshold    FLOAT NOT NULL,
                email        VARCHAR(256),
                note         VARCHAR(512),
                triggered    BOOLEAN NOT NULL DEFAULT FALSE,
                triggered_at TIMESTAMP,
                created_at   TIMESTAMP NOT NULL DEFAULT now()
            )
        """))
        # ── Intelligence reports: version uniqueness ───────────────────────────
        # create_all() only creates MISSING tables, so a table deployed yesterday never gains
        # today's constraint without this. COALESCE because NULL user_id means public context
        # and NULLs are distinct in a PostgreSQL unique index, which would leave the public
        # reports — the common case — entirely unprotected.
        # ── Earnings: period end and report-date provenance ────────────────────
        # create_all() only creates MISSING tables, so an existing table never gains a new
        # column without this. Required before any consumer can tell an announcement date from
        # a substituted period end — and the return calculation anchors on exactly that.
        conn.execute(text(
            "ALTER TABLE earnings_events ADD COLUMN IF NOT EXISTS period_end DATE"))
        conn.execute(text(
            "ALTER TABLE earnings_events ADD COLUMN IF NOT EXISTS "
            "report_date_source VARCHAR(32)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_earnings_report_date_source "
            "ON earnings_events (report_date_source)"))
        conn.execute(text(
            "ALTER TABLE earnings_events ADD COLUMN IF NOT EXISTS "
            "notification_suppressed_at TIMESTAMP"))
        conn.execute(text(
            "ALTER TABLE earnings_events ADD COLUMN IF NOT EXISTS "
            "notification_suppressed_reason VARCHAR(128)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_earnings_notification_suppressed "
            "ON earnings_events (notification_suppressed_at)"))

        # A cross-subject correction pointer. `supersedes_id` only links versions of one
        # subject, so a report describing the WRONG EVENT could never point at its replacement.
        conn.execute(text(
            "ALTER TABLE intelligence_reports ADD COLUMN IF NOT EXISTS corrected_by_id INTEGER"))
        conn.execute(text(
            "ALTER TABLE intelligence_reports ADD COLUMN IF NOT EXISTS correction JSON"))

        # Association evidence and the two separate hashes. `content_hash` widens because its
        # value is now prefixed with WHAT it covers; a bare digest could not distinguish a
        # transcription digest from a bytes digest, and the two support different claims.
        conn.execute(text(
            "ALTER TABLE issuer_documents ALTER COLUMN content_hash TYPE VARCHAR(80)"))
        conn.execute(text(
            "ALTER TABLE issuer_documents ADD COLUMN IF NOT EXISTS "
            "source_bytes_hash VARCHAR(80)"))
        conn.execute(text(
            "ALTER TABLE issuer_documents ADD COLUMN IF NOT EXISTS facts_hash VARCHAR(80)"))
        conn.execute(text(
            "ALTER TABLE issuer_documents ADD COLUMN IF NOT EXISTS association JSON"))

        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_intel_subject_type_owner_version
            ON intelligence_reports (subject_key, report_type, coalesce(user_id, -1), version)
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_price_alerts_user ON price_alerts (user_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_price_alerts_symbol ON price_alerts (symbol)"
        ))
        conn.execute(text("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS recurring BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMP"))
        # ── Signal alerts ──────────────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS signal_alerts (
                id           SERIAL PRIMARY KEY,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                symbol       VARCHAR(32) NOT NULL,
                email        VARCHAR(256),
                last_signal  VARCHAR(16),
                last_sent_at TIMESTAMP,
                created_at   TIMESTAMP NOT NULL DEFAULT now(),
                UNIQUE(user_id, symbol)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_signal_alerts_user ON signal_alerts (user_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_signal_alerts_symbol ON signal_alerts (symbol)"
        ))
        conn.execute(text(
            "ALTER TABLE signal_alerts ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMP"
        ))
        # ── Trade plan fill tracking ───────────────────────────────────────────
        conn.execute(text(
            "ALTER TABLE trade_plans ADD COLUMN IF NOT EXISTS actual_entry_price FLOAT"
        ))
        conn.execute(text(
            "ALTER TABLE trade_plans ADD COLUMN IF NOT EXISTS shares FLOAT"
        ))
        conn.execute(text(
            "ALTER TABLE trade_plans ADD COLUMN IF NOT EXISTS trading_style VARCHAR(16)"
        ))
        # ── Per-list trading style ─────────────────────────────────────────────
        conn.execute(text(
            "ALTER TABLE watchlists ADD COLUMN IF NOT EXISTS trading_style VARCHAR(16)"
        ))
        # ── Signal outcome tracking ────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id               BIGSERIAL PRIMARY KEY,
                signal_id        BIGINT NOT NULL UNIQUE REFERENCES signals(id) ON DELETE CASCADE,
                stock_id         INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                symbol           VARCHAR(32) NOT NULL,
                horizon          signalhorizon NOT NULL,
                signal_direction VARCHAR(8) NOT NULL,
                signal_date      DATE NOT NULL,
                confidence       FLOAT NOT NULL,
                fused_prob       FLOAT,
                ta_score         FLOAT,
                ml_prob          FLOAT,
                ml_auc           FLOAT,
                market_regime    VARCHAR(16),
                entry_date       DATE,
                entry_price      FLOAT,
                exit_date        DATE,
                exit_price       FLOAT,
                hold_days        INTEGER,
                pct_return       FLOAT,
                is_correct       BOOLEAN,
                ts_evaluated     TIMESTAMP NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_signal_outcomes_stock ON signal_outcomes (stock_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_signal_outcomes_symbol ON signal_outcomes (symbol)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_signal_outcomes_horizon ON signal_outcomes (horizon)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_signal_outcomes_signal_date ON signal_outcomes (signal_date)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_signal_outcomes_horizon_correct ON signal_outcomes (horizon, is_correct)"
        ))
        # alert_mode column added after initial signal_alerts creation
        conn.execute(text(
            "ALTER TABLE signal_alerts ADD COLUMN IF NOT EXISTS alert_mode VARCHAR(16) NOT NULL DEFAULT 'all'"
        ))
        # per-horizon alert subscriptions
        conn.execute(text(
            "ALTER TABLE signal_alerts ADD COLUMN IF NOT EXISTS horizon VARCHAR(16) NOT NULL DEFAULT 'SWING'"
        ))
        conn.execute(text(
            "ALTER TABLE signal_alerts ADD COLUMN IF NOT EXISTS require_consensus BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        # drop old single-stock unique constraint, replace with per-horizon one
        conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'signal_alerts_user_id_symbol_key'
                ) THEN
                    ALTER TABLE signal_alerts DROP CONSTRAINT signal_alerts_user_id_symbol_key;
                END IF;
            END $$;
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_alerts_user_symbol_horizon
            ON signal_alerts (user_id, symbol, horizon)
        """))
        # PT-A2: store market regime in equity curve for shading
        conn.execute(text(
            "ALTER TABLE paper_equity_curve ADD COLUMN IF NOT EXISTS market_regime VARCHAR(16)"
        ))
        # aud14-survivorship: delisted flag on stocks — included in ML training universe
        conn.execute(text(
            "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS delisted BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        # aud14-float-financials: migrate cash ledger columns to NUMERIC for exact arithmetic
        # Idempotent: only converts when the column is still DOUBLE PRECISION
        for _tbl, _col in [
            ("user_cash",        "amount"),
            ("user_positions",   "shares"),
            ("user_positions",   "avg_cost"),
            ("position_trades",  "shares"),
            ("position_trades",  "price"),
            ("paper_portfolios", "initial_capital"),
            ("paper_portfolios", "current_cash"),
            ("paper_trades",     "entry_price"),
            ("paper_trades",     "shares"),
            ("paper_trades",     "stop_loss"),
            ("paper_trades",     "take_profit"),
            ("paper_trades",     "current_stop"),
            ("paper_trades",     "exit_price"),
            ("paper_trades",     "pnl"),
            ("paper_trades",     "current_price"),
            ("paper_trades",     "highest_price"),
        ]:
            conn.execute(text(f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='{_tbl}' AND column_name='{_col}'
                          AND data_type='double precision'
                    ) THEN
                        ALTER TABLE {_tbl}
                            ALTER COLUMN {_col} TYPE NUMERIC(20,6)
                            USING {_col}::NUMERIC(20,6);
                    END IF;
                END $$;
            """))
        # Phase-1, T217-B, and T204: fundamentals columns added to ORM model without DB migration
        for _fund_col in ["peg_ratio", "debt_to_equity", "dividend_yield",
                          "short_percent_of_float", "short_ratio"]:
            conn.execute(text(
                f"ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS {_fund_col} FLOAT"
            ))
        # AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED: settlement date for short_percent_of_float/
        # short_ratio above — a real DATE column, not FLOAT, so it needs its own ALTER TABLE
        # rather than joining the loop above.
        conn.execute(text(
            "ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS short_interest_date DATE"
        ))
        # AUD-ALERTPREFS: create_all() only creates MISSING tables, which covers alert_preferences
        # on a fresh DB — but an existing deployment needs the unique constraint added explicitly,
        # and doing it here keeps the "adding a column/constraint to an existing table doesn't
        # auto-apply" rule from docs/incidents/docker-deploy-staleness.md satisfied.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alert_preferences (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                alert_type VARCHAR(64) NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                source VARCHAR(32),
                updated_at TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_preference "
            "ON alert_preferences (user_id, alert_type)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_alert_preferences_user_id ON alert_preferences (user_id)"
        ))
        # INT-8 forward-return tracking columns added to signal_outcomes after initial table creation
        for _col, _type in [
            ("price_5d",       "FLOAT"),
            ("return_5d",      "FLOAT"),
            ("is_correct_5d",  "BOOLEAN"),
            ("price_10d",      "FLOAT"),
            ("return_10d",     "FLOAT"),
            ("is_correct_10d", "BOOLEAN"),
            ("price_20d",      "FLOAT"),
            ("return_20d",     "FLOAT"),
            ("is_correct_20d", "BOOLEAN"),
            ("research_rec",   "VARCHAR(16)"),
            ("research_score", "FLOAT"),
        ]:
            conn.execute(text(
                f"ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS {_col} {_type}"
            ))
        # T208: SEC 8-K filings table for material event detection
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sec_filings (
                id          BIGSERIAL PRIMARY KEY,
                symbol      VARCHAR(32) NOT NULL,
                cik         VARCHAR(16) NOT NULL,
                accession   VARCHAR(32) NOT NULL UNIQUE,
                form        VARCHAR(16) NOT NULL DEFAULT '8-K',
                filed_date  DATE NOT NULL,
                report_date DATE,
                items       VARCHAR(512),
                description VARCHAR(512),
                is_material BOOLEAN NOT NULL DEFAULT FALSE,
                created_at  TIMESTAMP NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_sec_filings_symbol ON sec_filings (symbol)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_sec_filings_filed_date ON sec_filings (filed_date)"
        ))
        # T208: CIK column on stocks table for fast EDGAR lookup
        conn.execute(text(
            "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS cik VARCHAR(16)"
        ))
        # T11: index membership column on stocks
        conn.execute(text(
            "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS index_membership VARCHAR(256)"
        ))
        # T209: HKEX Stock Connect southbound flow table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hk_connect_flows (
                id              BIGSERIAL PRIMARY KEY,
                symbol          VARCHAR(32) NOT NULL,
                trade_date      DATE NOT NULL,
                net_buy_hkd     FLOAT,
                buy_hkd         FLOAT,
                sell_hkd        FLOAT,
                quota_used_pct  FLOAT,
                created_at      TIMESTAMP NOT NULL DEFAULT now(),
                UNIQUE(symbol, trade_date)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hk_connect_symbol ON hk_connect_flows (symbol)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hk_connect_date ON hk_connect_flows (trade_date)"
        ))
        # T220-F: fundamentals_snapshot for earnings revision momentum tracking
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                snapshot_date DATE NOT NULL,
                recommendation_mean FLOAT,
                eps_estimate FLOAT,
                revenue_growth FLOAT,
                earnings_growth FLOAT,
                return_on_equity FLOAT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_fundamentals_snapshot_sym_date
            ON fundamentals_snapshot (symbol, snapshot_date)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_fundamentals_snapshot_sym
            ON fundamentals_snapshot (symbol)
        """))
        # MOAT-1: financial_statements — multi-year filed statements, the prerequisite for a
        # real ROIC-persistence moat score. See FinancialStatement's own docstring
        # (shared/db/models.py) and docs/2026-09-06/SCOPING_QUANTITATIVE_MOAT_SCORE.md for why
        # neither `fundamentals` (~3 months of fetch-date rows) nor `fundamentals_snapshot`
        # (weekly, forward-accumulating only) can supply multi-year ROIC/margin durability.
        # All figures nullable: yfinance statement row labels vary by issuer/market, and a
        # missing line item must read as absent, never as a fabricated zero.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS financial_statements (
                id BIGSERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                period_end DATE NOT NULL,
                period_type VARCHAR(12) NOT NULL,
                total_revenue FLOAT,
                gross_profit FLOAT,
                operating_income FLOAT,
                ebit FLOAT,
                net_income FLOAT,
                tax_provision FLOAT,
                pretax_income FLOAT,
                total_assets FLOAT,
                total_debt FLOAT,
                total_equity FLOAT,
                cash_and_equivalents FLOAT,
                current_liabilities FLOAT,
                operating_cashflow FLOAT,
                capital_expenditure FLOAT,
                free_cashflow FLOAT,
                fetched_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_finstmt_sym_period
            ON financial_statements (symbol, period_end, period_type)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_finstmt_sym
            ON financial_statements (symbol)
        """))
        # OPTHIST-1: per-contract historical option chains from Unusual Whales. See
        # OptionChainHistory's own docstring (shared/db/models.py) for why every row is stored
        # rather than filtering to traded contracts (greeks are sparse but the full OI
        # distribution is needed for GEX/max-pain reconstruction), and why capture is
        # time-sensitive (UW's history is a ROLLING window, not an archive).
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS option_chain_history (
                id BIGSERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                as_of DATE NOT NULL,
                option_symbol VARCHAR(40) NOT NULL,
                expiry DATE,
                strike FLOAT,
                option_type VARCHAR(4),
                open_interest INTEGER,
                volume INTEGER,
                nbbo_bid FLOAT,
                nbbo_ask FLOAT,
                implied_volatility FLOAT,
                delta FLOAT,
                gamma FLOAT,
                theta FLOAT,
                vega FLOAT,
                rho FLOAT,
                fetched_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_optchain_sym_date_contract
            ON option_chain_history (symbol, as_of, option_symbol)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_optchain_sym_asof
            ON option_chain_history (symbol, as_of)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_optchain_expiry
            ON option_chain_history (expiry)
        """))
        # OPTHIST-1 follow-up: drop the duplicate indexes create_all() built from the model's
        # original per-column index=True flags (now removed). Each of these fully duplicates
        # coverage the composite/unique indexes above already provide:
        #   ix_option_chain_history_as_of   <- ix_optchain_sym_asof (symbol, as_of)
        #   ix_option_chain_history_expiry  <- ix_optchain_expiry (expiry)
        #   ix_option_chain_history_symbol  <- leading col of uq_optchain_sym_date_contract
        # Caught by measuring the live table mid-backfill rather than by reading the schema:
        # at 253k rows the table carried 7 indexes / 32MB against a 40MB heap. On a table that
        # grows ~4,100 rows per symbol-day this compounds fast, so it is worth dropping rather
        # than tolerating. Safe/idempotent: IF EXISTS, and dropping a redundant index cannot
        # break a query, only make it use the equivalent remaining index.
        for _dup_ix in (
            "ix_option_chain_history_as_of",
            "ix_option_chain_history_expiry",
            "ix_option_chain_history_symbol",
        ):
            conn.execute(text(f"DROP INDEX IF EXISTS {_dup_ix}"))

        # AUD-UWEXPAND: three tables for endpoints the API BASIC tier already grants but that
        # nothing consumed. Deliberately no per-column index=True on any of these — see the
        # OPTHIST-DUPINDEX comment above for why that duplicates the composite indexes below.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS etf_fund_flows (
                id BIGSERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                as_of DATE NOT NULL,
                change_shares DOUBLE PRECISION,
                change_premium DOUBLE PRECISION,
                close DOUBLE PRECISION,
                volume DOUBLE PRECISION,
                expiration_cycle VARCHAR(16),
                is_fomc BOOLEAN,
                fetched_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_etf_flow_symbol_date
            ON etf_fund_flows (symbol, as_of)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_etfflow_sym_asof ON etf_fund_flows (symbol, as_of)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fda_catalysts (
                id BIGSERIAL PRIMARY KEY,
                unique_identifier VARCHAR(120) NOT NULL,
                ticker VARCHAR(20),
                catalyst VARCHAR(120),
                event_type VARCHAR(120),
                drug VARCHAR(255),
                indication VARCHAR(255),
                status VARCHAR(64),
                description TEXT,
                outcome TEXT,
                outcome_brief TEXT,
                start_date DATE,
                end_date DATE,
                -- FREE TEXT on purpose ("2025-MID", "2025-H2"). Never a DATE column.
                target_date_text VARCHAR(64),
                has_options BOOLEAN,
                marketcap DOUBLE PRECISION,
                source_link TEXT,
                fetched_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_fda_catalyst_uid
            ON fda_catalysts (unique_identifier)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_fda_ticker_start ON fda_catalysts (ticker, start_date)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS institutional_ownership (
                id BIGSERIAL PRIMARY KEY,
                ticker VARCHAR(20) NOT NULL,
                institution VARCHAR(255) NOT NULL,
                cik VARCHAR(20),
                report_date DATE NOT NULL,
                filing_date DATE,
                units DOUBLE PRECISION,
                units_changed DOUBLE PRECISION,
                value DOUBLE PRECISION,
                avg_price DOUBLE PRECISION,
                shares_outstanding DOUBLE PRECISION,
                is_hedge_fund BOOLEAN,
                fetched_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_instown_tkr_inst_date
            ON institutional_ownership (ticker, institution, report_date)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_instown_ticker_report
            ON institutional_ownership (ticker, report_date)
        """))
        # T234-ML-FUND-BROADCAST-LEAKAGE: extend fundamentals_snapshot with the columns
        # builder.py broadcasts today's value for across ALL historical training rows
        # (lookahead bias). Backfilling these lets a future point-in-time merge_asof join
        # replace the broadcast, same pattern already used for revenue_growth/earnings_growth/
        # return_on_equity/recommendation_mean since T228.
        for _col, _type in [
            ("gross_margin", "FLOAT"), ("fcf_yield", "FLOAT"), ("short_ratio", "FLOAT"),
            ("short_ratio_delta", "FLOAT"), ("short_percent_of_float", "FLOAT"),
            ("price_to_book", "FLOAT"), ("peg_ratio", "FLOAT"), ("debt_to_equity", "FLOAT"),
            ("ddm_discount", "FLOAT"), ("piotroski_score", "FLOAT"),
        ]:
            conn.execute(text(
                f"ALTER TABLE fundamentals_snapshot ADD COLUMN IF NOT EXISTS {_col} {_type}"
            ))
        # T249-EARNINGS-LLM-IMPACT: LLM-generated earnings impact read, mirroring
        # EconomicEvent's reaction_text/reaction_generated_at/reaction_sent_at/sectors_helped/
        # sectors_hurt columns exactly.
        conn.execute(text(
            "ALTER TABLE earnings_events ADD COLUMN IF NOT EXISTS impact_text TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE earnings_events ADD COLUMN IF NOT EXISTS impact_generated_at TIMESTAMP"
        ))
        conn.execute(text(
            "ALTER TABLE earnings_events ADD COLUMN IF NOT EXISTS impact_sent_at TIMESTAMP"
        ))
        conn.execute(text(
            "ALTER TABLE earnings_events ADD COLUMN IF NOT EXISTS sectors_helped TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE earnings_events ADD COLUMN IF NOT EXISTS sectors_hurt TEXT"
        ))
        # T232-SIG10-SELLGATE: bearish-pillar count backfilled onto existing signal_outcomes
        # rows — see SignalOutcome.bearish_pillars_active's own docstring for why this can't
        # be copied live from Signal.reasons the way market_regime is.
        conn.execute(text(
            "ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS bearish_pillars_active INTEGER"
        ))
        # AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH: drop the old fiscal-quarter
        # uniqueness constraint (which silently collided two genuine reports in the same
        # calendar quarter, upsert-overwriting one with the other) and replace it with one
        # keyed on report_date — see EarningsEvent.__table_args__'s own comment in models.py.
        conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_earnings_stock_period'
                ) THEN
                    ALTER TABLE earnings_events DROP CONSTRAINT uq_earnings_stock_period;
                END IF;
            END $$;
        """))
        # A pre-existing DB can genuinely have duplicate (stock_id, report_date) rows already —
        # the old constraint never prevented that (different fiscal_year/fiscal_quarter values
        # could share the same real report_date, e.g. the fiscal-quarter-collision bug this fix
        # closes). Keep only the most-recently-fetched row per (stock_id, report_date) before
        # adding the new constraint, or the ADD CONSTRAINT below would fail outright.
        conn.execute(text("""
            DELETE FROM earnings_events a USING earnings_events b
            WHERE a.stock_id = b.stock_id
              AND a.report_date = b.report_date
              AND a.id < b.id
        """))
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_earnings_stock_report_date'
                ) THEN
                    ALTER TABLE earnings_events
                    ADD CONSTRAINT uq_earnings_stock_report_date UNIQUE (stock_id, report_date);
                END IF;
            END $$;
        """))

        # AUD262-ENTRY-EXIT-COMMISSION-EXCLUDED-FROM-PNL: entry commission was deducted from
        # cash at open but never stored on the trade, so trade.pnl couldn't reconcile to it at
        # close. Currently latent (commission_per_share defaults to 0.0) but corrupts every
        # P&L metric the moment a real commission is configured.
        conn.execute(text(
            "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_commission FLOAT"
        ))

        # AUD265-CPRATIO-CENSORED-BREAKS-RANKING: cp_ratio was persisted pre-capped at 10.0 —
        # every symbol whose real call/put ratio exceeded 10.0 collapsed to the same stored
        # value, corrupting the pre-market brief's |cp_ratio - 1| ranking, not just its display.
        conn.execute(text(
            "ALTER TABLE options_flow_snapshots ADD COLUMN IF NOT EXISTS cp_ratio_uncapped FLOAT"
        ))

        # T264-SHORTSQUEEZE-PREBREAKOUT-CONFIDENCE: two honestly-scoped confidence signals
        # added alongside the still-untrained model_confidence/model_version columns — see
        # PreBreakoutAlertOutcome's own docstring for why a real squeeze-breakout classifier
        # remains deferred (thin data) and why these two exist instead.
        conn.execute(text(
            "ALTER TABLE prebreakout_alert_outcomes ADD COLUMN IF NOT EXISTS ml_price_direction_confidence FLOAT"
        ))
        conn.execute(text(
            "ALTER TABLE prebreakout_alert_outcomes ADD COLUMN IF NOT EXISTS ml_price_direction_model_version VARCHAR(64)"
        ))
        conn.execute(text(
            "ALTER TABLE prebreakout_alert_outcomes ADD COLUMN IF NOT EXISTS calibrated_win_rate FLOAT"
        ))
        conn.execute(text(
            "ALTER TABLE prebreakout_alert_outcomes ADD COLUMN IF NOT EXISTS calibrated_win_rate_count INTEGER"
        ))

        # DESIGN_SQUEEZE_ALERT_PERFORMANCE_MEASUREMENT: 1d/2d/3d forward-return windows on
        # both squeeze/gamma-unwind and prebreakout outcome tables — answers "will the price
        # go up the next day or later" without waiting the full 5-calendar-day window the
        # pre-existing 5d/10d/20d columns require.
        for _tbl in ("squeeze_alert_outcomes", "prebreakout_alert_outcomes"):
            for _w in (1, 2, 3):
                conn.execute(text(
                    f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS price_{_w}d FLOAT"
                ))
                conn.execute(text(
                    f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS return_{_w}d FLOAT"
                ))
                conn.execute(text(
                    f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS is_correct_{_w}d BOOLEAN"
                ))

        # T322-FEATURE-TIERING: users.tier is a NEW enum-typed column on an EXISTING,
        # already-populated table — SAEnum(UserTier)'s Postgres type name is lowercased from
        # the class name ("usertier"), matching how `role`'s own "userrole" type was already
        # implicitly created by create_all() the first time this table was ever built. Postgres
        # has no native `CREATE TYPE ... IF NOT EXISTS`; guard via a DO block catching the
        # duplicate_object exception so this stays idempotent on every restart, matching every
        # other statement in this function.
        conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE usertier AS ENUM ('BASIC', 'ADVANCED');
            EXCEPTION WHEN duplicate_object THEN null;
            END $$;
        """))
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS tier usertier NOT NULL DEFAULT 'BASIC'"
        ))

        # AUD-SIGNAL3-EVALSELECTIONBIAS: 4 new nullable columns on the EXISTING signals table —
        # see Signal model's own docstring (shared/db/models.py) for the full rationale. Reuses
        # the ALREADY-EXISTING `signaltype` enum (created by create_all() for this table's own
        # `signal` column) — no new CREATE TYPE needed, confirmed live against production before
        # writing this migration.
        conn.execute(text(
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS first_buy_sell_at TIMESTAMP"
        ))
        conn.execute(text(
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS first_buy_sell_signal signaltype"
        ))
        conn.execute(text(
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS first_buy_sell_confidence FLOAT"
        ))
        conn.execute(text(
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS first_buy_sell_bullish_probability FLOAT"
        ))
        conn.execute(text(
            "ALTER TABLE signals ADD COLUMN IF NOT EXISTS first_buy_sell_reasons JSON"
        ))


def _apply_isolated_ddl() -> None:
    """Post-hoc schema changes that must each succeed or fail ALONE.

    CORRECTED 2026-09-28 (pre-deployment audit). Both statements below were originally written
    inside `_seed_admin()`, which was wrong twice over:

      1. `_seed_admin()` RETURNS EARLY when `admin_password` is unset — and it is unset in
         production. So neither statement would have run at all: R06's unique intent key and
         R08's `mark_evidence` column would both have been dead on arrival, while every test
         asserting "the DDL exists in session.py" kept passing. This is the same class as the
         create_all()-only-creates-tables incident these statements exist to work around, one
         level up: the migration was written correctly and then placed where it does not run.

      2. The index creation was wrapped in `try/except` with `conn.rollback()` INSIDE a
         `with engine.begin()` block. Rolling back there closes the transaction, so every
         later statement in that block raises InvalidRequestError — and the rollback would
         also have discarded the admin seeding already done in the same transaction. A guard
         that takes down the thing it was guarding.

    Each statement therefore gets its OWN transaction, and its own try/except around the whole
    `with`, so a failure rolls back nothing but itself.
    """
    statements = [
        # M24/M25 (2026-10-02): the capability matrix caught this on its FIRST production run.
        # `paper_trades` already existed, so `create_all()` never added the broker-submission
        # columns and both `broker_submission` and `submission_reconciliation` reported
        # not_ready — correctly, since the durable submission path genuinely could not work.
        # Exactly the create_all()-only-creates-tables incident this list exists for, found by
        # a readiness check rather than by a failure in production.
        ("broker submission state columns",
         "ALTER TABLE paper_trades "
         "ADD COLUMN IF NOT EXISTS broker_submission_state VARCHAR(16), "
         "ADD COLUMN IF NOT EXISTS broker_submit_attempts INTEGER NOT NULL DEFAULT 0, "
         "ADD COLUMN IF NOT EXISTS broker_client_order_id VARCHAR(64), "
         "ADD COLUMN IF NOT EXISTS broker_submission_path VARCHAR(16), "
         "ADD COLUMN IF NOT EXISTS broker_submitted_at TIMESTAMP"),
        # The identity's UNIQUENESS is the capability, not the column: without it a retry could
        # mint a second id for the same intent and an `unknown` would match two orders.
        # Separate statement so a pre-existing duplicate cannot block the columns above.
        ("uq_paper_trades_broker_client_order_id",
         "CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_trades_broker_client_order_id "
         "ON paper_trades (broker_client_order_id) "
         "WHERE broker_client_order_id IS NOT NULL"),
        # R08: create_all() only creates MISSING TABLES, so a column added to an existing table
        # never appears from the model declaration alone.
        ("mark_evidence column",
         "ALTER TABLE options_income_equity_curve "
         "ADD COLUMN IF NOT EXISTS mark_evidence JSONB"),
        # R06: same reason for the unique Index() declared on OptionsIncomePosition. This one
        # CAN legitimately fail — if the table already holds duplicate
        # (portfolio_id, option_symbol, entry_date) rows from before the guard existed, the
        # CREATE is rejected. Startup must not die for that: the row lock and the lease check
        # remain in force, and the failure has to be visible rather than silent.
        ("uq_options_income_intent",
         "CREATE UNIQUE INDEX IF NOT EXISTS uq_options_income_intent "
         "ON options_income_positions (portfolio_id, option_symbol, entry_date)"),
    ]
    for name, sql in statements:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except Exception as exc:          # noqa: BLE001 — see the docstring
            print(f"[init_db] WARNING {name} not applied: {exc}")


# EC-01 (2026-09-29 email-fix closure review): the deploy watermark for the legacy price-alert
# closeout below. Rows triggered BEFORE this instant predate `last_sent_at` recording delivery;
# rows triggered at or after it are unambiguous, because the sender stamps on success from then
# on. A pending row from after the watermark is a REAL undelivered notification and must survive.
_PRICE_ALERT_DELIVERY_WATERMARK = "2026-09-28 00:00:00+00"


# EC-03 (2026-09-29): migration readiness. `_apply_once` used to report a failure with print()
# and return, and `/health` reported "ok" unconditionally — so a service whose migration never
# applied started, passed its healthcheck, and ran every job that depended on it. A successful
# process startup is not proof that its prerequisites were applied.
#
# Failures are recorded here so they survive the function that produced them, and are readable by
# two consumers: the health endpoint (visibility) and the jobs that depend on them (enforcement).
_MIGRATION_FAILURES: dict[str, str] = {}

# What STOPS WORKING when a given migration has not applied, in operator language.
#
# EC-03 follow-up: a health block reporting only `{"ok": false, "failed": ["<name>"]}` tells a
# monitor that something is wrong but not what it costs, and a migration name is not a capability.
# Keeping the container alive must not make a failure look operationally healthy — so the block
# says, in words, which behaviour is currently degraded and how it degrades.
#
# A migration absent from this map still reports as failed; it simply has no capability statement
# yet, which is honest rather than silent.
_MIGRATION_CAPABILITIES: dict[str, str] = {
    "2026-09-28-legacy-price-alert-delivery-closeout": (
        "price-alert delivery retries are SKIPPED — undelivered alerts stay pending and are "
        "retried once this migration applies; no alert is lost, none is re-sent"
    ),
}


def migration_state() -> dict:
    """What this process knows about its own one-shot migrations.

    Deliberately reports what THIS process attempted, not a query of the ledger: a service that
    started before a migration was written has nothing to report and is not broken, while one
    that tried and failed is. The two are different states and a ledger query would conflate them.
    """
    return {
        "ok": not _MIGRATION_FAILURES,
        "failed": sorted(_MIGRATION_FAILURES),
        "detail": dict(_MIGRATION_FAILURES),
        # The operator-facing half: what is actually degraded right now. A monitor must read
        # THIS, not the top-level `status`, which stays "ok" so that one unapplied data
        # migration cannot cascade through `depends_on: service_healthy` into a refusal to start.
        "degraded": [
            _MIGRATION_CAPABILITIES.get(name, f"unknown capability (migration {name!r})")
            for name in sorted(_MIGRATION_FAILURES)
        ],
    }


def migration_applied(name: str) -> bool | None:
    """Has `name` been applied to this database, per the ledger?

    Returns None — not False — when the answer cannot be established (no ledger table yet, the
    database unreachable). A caller gating a dependent job must treat None as "unknown" and decide
    deliberately; collapsing it to False would make an unreachable database look like a definite
    "not applied", and collapsing it to True would run the job on an unverified prerequisite.
    """
    if name in _MIGRATION_FAILURES:
        return False
    try:
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT 1 FROM applied_migrations WHERE name = :name"
            ), {"name": name}).first()
        return row is not None
    except Exception:
        return None


def _apply_once(name: str, sql: str, params: dict | None = None) -> None:
    """Run `sql` exactly once across the lifetime of this database, ever.

    EC-01 — WHY THIS EXISTS, AND WHY THE PREVIOUS ATTEMPT WAS WORSE THAN NO MIGRATION.

    The legacy price-alert closeout used to sit in `_apply_isolated_ddl()`'s plain statement
    list, carrying a comment that called it "one-shot: after this runs there are no
    NULL-timestamped triggered rows left to match". That reasoning is simply wrong, and the
    closure review caught it. `init_db()` runs `_apply_isolated_ddl()` on EVERY startup of
    every one of the twelve backend services. New NULL-timestamped triggered rows appear
    constantly — that is precisely what a FAILED SEND looks like, and producing them is the
    entire point of the retry mechanism the same remediation added. So the next restart of any
    service would stamp every genuinely-pending alert as delivered, with zero transport calls,
    and the retry query (which requires `last_sent_at IS NULL`) would never see it again.

    A migration that silently deletes the evidence of the failure it was written to disambiguate
    is worse than not having run it: the retry feature would have looked implemented and been
    inert after the first restart. Measured on production 2026-09-29 before this fix: 0 rows
    were in that pending state, so the damage had not yet occurred — this is a latent defect
    being closed, not an incident being cleaned up.

    The guard is a real ledger, not a comment. The INSERT and the statement share ONE
    transaction, so two services starting simultaneously cannot both run it: the second blocks
    on the primary key until the first commits, then conflicts, inserts nothing, and skips.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS applied_migrations ("
                "  name VARCHAR(160) PRIMARY KEY,"
                "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                ")"
            ))
    except Exception as exc:  # noqa: BLE001
        _MIGRATION_FAILURES[name] = f"ledger unavailable: {exc}"
        print(f"[init_db] WARNING applied_migrations ledger unavailable: {exc}")
        return

    try:
        with engine.begin() as conn:
            claimed = conn.execute(text(
                "INSERT INTO applied_migrations (name) VALUES (:name) "
                "ON CONFLICT (name) DO NOTHING"
            ), {"name": name}).rowcount
            if not claimed:
                return  # already applied, by this or another service, at some point in the past
            conn.execute(text(sql), params or {})
        _MIGRATION_FAILURES.pop(name, None)
    except Exception as exc:  # noqa: BLE001 — same rationale as _apply_isolated_ddl's own
        _MIGRATION_FAILURES[name] = str(exc)
        print(f"[init_db] WARNING one-shot migration {name} not applied: {exc}")


def _apply_one_shot_migrations() -> None:
    """One-shot data migrations — each runs exactly once per database, guarded by the ledger.

    Distinct from `_apply_isolated_ddl()`, whose statements are all IDEMPOTENT by construction
    (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) and are therefore safe — and
    correct — to re-run on every startup. Anything that MUTATES ROWS belongs here instead: re-
    running it is not a no-op, and "it will not match anything the second time" is a claim about
    data, which changes, not about schema, which does not.
    """
    _apply_once(
        "2026-09-28-legacy-price-alert-delivery-closeout",
        # EF-03: close out the AMBIGUOUS LEGACY ROWS. `last_sent_at` only began recording
        # notification delivery for price alerts on 2026-09-28. A row triggered before that has
        # it NULL whether or not its email actually went out, so the retry query cannot tell
        # "never delivered" from "delivered before we started recording it" — and re-sending
        # would mail people price alerts that are weeks old.
        #
        # The audit's guidance was explicit: do not interpret every legacy null as a failed
        # delivery. These are stamped from their own trigger time, which both marks them
        # not-retryable and leaves them distinguishable afterwards (`last_sent_at = triggered_at`
        # exactly, which a real send never produces — it stamps strictly later).
        #
        # The watermark is the second guard, independent of the ledger. Even on a database where
        # the ledger were somehow lost, this can no longer touch a post-fix pending row.
        "UPDATE price_alerts SET last_sent_at = triggered_at "
        "WHERE triggered IS TRUE AND last_sent_at IS NULL AND triggered_at IS NOT NULL "
        "AND triggered_at < :watermark",
        {"watermark": _PRICE_ALERT_DELIVERY_WATERMARK},
    )


def _seed_admin() -> None:
    try:
        import bcrypt as _bcrypt
    except ImportError:
        return  # bcrypt not available in non-auth services

    raw_pw = _settings.admin_password
    if not raw_pw:
        return  # no password configured — skip seeding (admin created manually)

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM users WHERE username = 'lausing'")
        ).fetchone()
        if not row:
            hashed = _bcrypt.hashpw(raw_pw.encode(), _bcrypt.gensalt()).decode()
            result = conn.execute(
                text("""
                    INSERT INTO users (username, password_hash, role, is_active, created_at)
                    VALUES ('lausing', :hash, 'ADMIN', true, now())
                    RETURNING id
                """),
                {"hash": hashed},
            )
            admin_id = result.fetchone()[0]
        else:
            admin_id = row[0]

        # Assign orphaned watchlist items (user_id IS NULL) to admin
        conn.execute(
            text("UPDATE watchlist_items SET user_id = :uid WHERE user_id IS NULL"),
            {"uid": admin_id},
        )

        # AUD19-ARCH1: Seed service accounts so service JWT tokens (sub="scheduler",
        # sub="paper-engine") resolve via get_current_user DB lookup when called via HTTP.
        # These users have no usable password — login is blocked; only service JWTs work.
        for _svc_user in ("scheduler", "paper-engine"):
            _exists = conn.execute(
                text("SELECT 1 FROM users WHERE username = :u"), {"u": _svc_user}
            ).fetchone()
            if not _exists:
                conn.execute(
                    text("""
                        INSERT INTO users (username, password_hash, role, is_active, created_at)
                        VALUES (:u, 'SERVICE_ACCOUNT_NO_LOGIN', 'ADMIN', true, now())
                    """),
                    {"u": _svc_user},
                )
