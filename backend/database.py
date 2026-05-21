import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

def _is_unresolved_template(value: str) -> bool:
    """Return True if the value still contains Railway reference variable syntax."""
    return "${{" in value or "}}" in value


def _resolve_database_url() -> str:
    """
    Attempt to build a DATABASE_URL from individual PG* variables first.
    Falls back to the DATABASE_URL env var, then a hardcoded local default.
    """
    # --- Strategy 1: individual PG* variables ---
    pg_vars = {
        "PGHOST":     os.getenv("PGHOST"),
        "PGUSER":     os.getenv("PGUSER"),
        "PGPASSWORD": os.getenv("PGPASSWORD"),
        "PGPORT":     os.getenv("PGPORT", "5432"),
        "PGDATABASE": os.getenv("PGDATABASE"),
    }

    logger.info("Checking individual PG* environment variables:")
    for name, value in pg_vars.items():
        if value is None:
            logger.info("  %s = <not set>", name)
        elif _is_unresolved_template(value):
            logger.warning("  %s = %r  ← UNRESOLVED TEMPLATE — Railway reference variable not substituted", name, value)
        else:
            # Mask the password in logs
            display = "***" if name == "PGPASSWORD" else value
            logger.info("  %s = %r", name, display)

    all_set = all(v is not None for v in pg_vars.values())
    any_unresolved = any(
        v is not None and _is_unresolved_template(v) for v in pg_vars.values()
    )

    if all_set and not any_unresolved:
        url = (
            f"postgresql+asyncpg://{pg_vars['PGUSER']}:{pg_vars['PGPASSWORD']}"
            f"@{pg_vars['PGHOST']}:{pg_vars['PGPORT']}/{pg_vars['PGDATABASE']}"
        )
        logger.info("Strategy 1 succeeded — constructed DATABASE_URL from PG* variables")
        return url

    if any_unresolved:
        logger.error(
            "Strategy 1 failed — one or more PG* variables contain unresolved Railway "
            "reference variable syntax. Railway did not substitute the placeholders before "
            "the process started. Falling back to DATABASE_URL env var."
        )
    else:
        logger.warning(
            "Strategy 1 skipped — not all PG* variables are set (%s missing). "
            "Falling back to DATABASE_URL env var.",
            ", ".join(k for k, v in pg_vars.items() if v is None),
        )

    # --- Strategy 2: DATABASE_URL env var ---
    raw_url = os.getenv("DATABASE_URL")
    if raw_url:
        if _is_unresolved_template(raw_url):
            logger.error(
                "Strategy 2 failed — DATABASE_URL contains unresolved Railway reference "
                "variable syntax: %r. Falling back to local default.", raw_url
            )
        else:
            logger.info("Strategy 2 succeeded — using DATABASE_URL env var")
            return raw_url

    # --- Strategy 3: local development default ---
    default_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/xvoice"
    logger.warning("Strategy 3 — falling back to hardcoded local default: %s", default_url)
    return default_url


DATABASE_URL = _resolve_database_url()

# Railway gives postgresql:// but asyncpg needs postgresql+asyncpg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

logger.info("Final DATABASE_URL scheme: %s", DATABASE_URL.split("://")[0])

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Create all tables on startup and handle minor schema updates."""
    from sqlalchemy import text
    async with engine.begin() as conn:
        # 1. Create tables if they don't exist
        await conn.run_sync(Base.metadata.create_all)
        
        # 2. Lazy migration: Ensure audio_duration_seconds exists in word_records
        # Railway/Postgres supports 'ADD COLUMN IF NOT EXISTS'
        try:
            await conn.execute(text("ALTER TABLE word_records ADD COLUMN IF NOT EXISTS audio_duration_seconds FLOAT"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER DEFAULT 0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC'"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255)"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255)"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMP"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_expired_email_sent BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_hotkey VARCHAR(20) DEFAULT 'f8'"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(10) DEFAULT 'en'"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_translation_enabled BOOLEAN DEFAULT FALSE"))
        except Exception as e:
            # Log but don't block startup if migration fails (e.g. non-postgres DB during local testing)
            print(f"Lazy migration notice: {e}")
