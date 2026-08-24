import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/xvoice")

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
        await conn.run_sync(Base.metadata.create_all)

    # ── Lazy migrations ─────────────────────────────────────────────────────
    #
    # Each statement runs in its own transaction, and a failure is logged and
    # stepped over rather than abandoning the rest.
    #
    # These used to share one try block and one transaction. Postgres aborts a
    # transaction on the first error, so a single statement failing — a table
    # that does not exist yet on a fresh database, say — silently skipped every
    # migration after it. The app then started normally against a half-migrated
    # schema, which is the worst of both outcomes: no crash to notice, and
    # columns the code expects quietly absent.
    #
    # Every statement here is idempotent, so re-running on each boot is free.
    migrations = [
        "ALTER TABLE word_records ADD COLUMN IF NOT EXISTS audio_duration_seconds FLOAT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_expired_email_sent BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_hotkey VARCHAR(20) DEFAULT 'f8'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(10) DEFAULT 'en'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_translation_enabled BOOLEAN DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_expires TIMESTAMP",

        # ── Per-product subscriptions ───────────────────────────────────────
        # Dictation, Writing and Platform are sold separately, so each needs its
        # own status and period end. One shared subscription_status meant one
        # product's expiry took the others down with it, and plan_product could
        # only ever name a single product.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS dictation_sub_status VARCHAR(20)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS dictation_period_end TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS dictation_sub_id VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS writing_sub_status VARCHAR(20)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS writing_period_end TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS writing_sub_id VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_sub_status VARCHAR(20)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_period_end TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_sub_id VARCHAR(255)",

        # Backfill from the old single-subscription columns. Only rows that have
        # never been touched (all three statuses NULL) and that hold a live
        # subscription are seeded, so this is safe to re-run and cannot
        # overwrite anything set since.
        """
            UPDATE users
            SET dictation_sub_status = lower(subscription_status::text),
            dictation_period_end = current_period_end,
            dictation_sub_id     = stripe_subscription_id
            WHERE lower(trim(plan_product)) = 'dictation'
            AND dictation_sub_status IS NULL
            AND writing_sub_status  IS NULL
            AND platform_sub_status IS NULL
            AND lower(subscription_status::text) IN ('paid', 'canceled')
        """,
        """
            UPDATE users
            SET writing_sub_status = lower(subscription_status::text),
            writing_period_end = current_period_end,
            writing_sub_id     = stripe_subscription_id
            WHERE lower(trim(plan_product)) = 'writing'
            AND dictation_sub_status IS NULL
            AND writing_sub_status  IS NULL
            AND platform_sub_status IS NULL
            AND lower(subscription_status::text) IN ('paid', 'canceled')
        """,
        """
            UPDATE users
            SET platform_sub_status = lower(subscription_status::text),
            platform_period_end = current_period_end,
            platform_sub_id     = stripe_subscription_id
            WHERE lower(trim(plan_product)) = 'platform'
            AND dictation_sub_status IS NULL
            AND writing_sub_status  IS NULL
            AND platform_sub_status IS NULL
            AND lower(subscription_status::text) IN ('paid', 'canceled')
        """,

        # Statuses are stored lower-case; the enum renders as its NAME in
        # Postgres, so normalise whatever the backfill copied in.
        "UPDATE users SET dictation_sub_status = lower(dictation_sub_status) WHERE dictation_sub_status IS NOT NULL",
        "UPDATE users SET writing_sub_status = lower(writing_sub_status) WHERE writing_sub_status IS NOT NULL",
        "UPDATE users SET platform_sub_status = lower(platform_sub_status) WHERE platform_sub_status IS NOT NULL",
    ]

    failed = 0
    for statement in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(statement))
        except Exception as e:
            # Expected on SQLite (no ADD COLUMN IF NOT EXISTS) and on a fresh
            # database where a table is not there yet. Logged individually so a
            # genuine failure is visible instead of hidden behind an earlier one.
            failed += 1
            logger.warning(f"Migration skipped: {statement.strip().splitlines()[0][:90]} — {e}")

    if failed:
        logger.warning(f"{failed} of {len(migrations)} lazy migrations did not apply.")
    else:
        logger.info(f"All {len(migrations)} lazy migrations applied or already present.")
