import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Railway often automatically injects individual PG variables instead of DATABASE_URL.
# If they are present and represent a remote Railway database, prioritize constructing DATABASE_URL dynamically.
pg_host = os.getenv("PGHOST") or os.getenv("POSTGRES_HOST")
if pg_host and pg_host not in ("localhost", "127.0.0.1", "db"):
    pg_user = os.getenv("PGUSER") or os.getenv("POSTGRES_USER")
    pg_password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD")
    pg_port = os.getenv("PGPORT") or os.getenv("POSTGRES_PORT", "5432")
    pg_db = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or os.getenv("POSTGRES_DATABASE")
    if pg_user and pg_password and pg_db:
        DATABASE_URL = f"postgresql+asyncpg://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}"

# Fallback to local default if still not set
if not DATABASE_URL:
    DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/xvoice"

# Railway gives postgresql:// but asyncpg needs postgresql+asyncpg://
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
