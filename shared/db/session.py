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
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_price_alerts_user ON price_alerts (user_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_price_alerts_symbol ON price_alerts (symbol)"
        ))
        # ── Signal alerts ──────────────────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS signal_alerts (
                id           SERIAL PRIMARY KEY,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                symbol       VARCHAR(32) NOT NULL,
                email        VARCHAR(256),
                last_signal  VARCHAR(16),
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


def _seed_admin() -> None:
    try:
        import bcrypt as _bcrypt
    except ImportError:
        return  # bcrypt not available in non-auth services

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM users WHERE username = 'lausing'")
        ).fetchone()
        if not row:
            hashed = _bcrypt.hashpw(b"120402", _bcrypt.gensalt()).decode()
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
