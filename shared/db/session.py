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


def _run_migrations() -> None:
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
        # Add per-user unique constraint if not already present
        conn.execute(text("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_watchlist_user_stock'
                ) THEN
                    ALTER TABLE watchlist_items
                    ADD CONSTRAINT uq_watchlist_user_stock UNIQUE (user_id, stock_id);
                END IF;
            END $$
        """))


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
