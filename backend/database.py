import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Connection string is sourced exclusively from DATABASE_URL.
# Set this variable directly in the Railway Variables tab as a plain connection
# string (e.g. postgresql://user:pass@host:5432/db).  Do NOT use reference
# variables like ${{ Postgres.PGHOST }} — they are not resolved at runtime and
# will be passed as literal strings, causing authentication failures.
_LOCAL_DEFAULT = "postgresql+asyncpg://postgres:postgres@localhost:5432/xvoice"
DATABASE_URL = os.getenv("DATABASE_URL", _LOCAL_DEFAULT)

if DATABASE_URL == _LOCAL_DEFAULT:
    logger.warning("DATABASE_URL is not set — falling back to local default.")
else:
    logger.info("DATABASE_URL loaded from environment.")

# Normalise the scheme: asyncpg requires postgresql+asyncpg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

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
