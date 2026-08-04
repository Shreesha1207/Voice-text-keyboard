from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from models import Achievement
from database import async_sessionmaker, engine, AsyncSessionLocal
import asyncio
from worker import start_worker

from routers import auth, stats, achievements, billing, transcribe, transform
from routers import writing_prefs
from routers import events
from models import Achievement, WritingAction, WritingPreferences  # noqa: F401 — ensures init_db creates all tables

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="xvoice API", description="Backend for the Gamified Voice-Text Keyboard App")

# --- Middlewares ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://localhost:5173",
        # Lovable preview URLs
        "https://preview--happy-tiny-glance.lovable.app",
        "https://id-preview--29629b16-9d9f-4a0b-963b-efdedb055e28.lovable.app",
        # Production — both /dashboard (dictation) and /writing/dashboard share this origin.
        # allow_credentials=True requires an exact origin match, so the www host has to
        # be listed separately; without it every authenticated call from
        # https://www.xvoicekeyboard.com fails CORS preflight.
        "https://xvoicekeyboard.com",
        "https://www.xvoicekeyboard.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    logger.info("Initializing database...")
    await init_db()

    # Surface missing STRIPE_*_PRICE_ID config in the deploy log. Without it a
    # subscription to the unmapped product silently leaves plan_product at its
    # default, and that product never unlocks for the customer.
    billing.log_plan_product_config()

    # Start both the transcription worker AND the trial expiry checker
    start_worker()
    
    # Seed Achievements if empty
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        res = await db.execute(select(Achievement))
        if len(res.scalars().all()) == 0:
             logger.info("Seeding initial achievements")
             seed_data = [
                 Achievement(slug="first_word", name="First Word", description="First successful transcription", icon="🎙️", trigger_type="total_words", trigger_value="1"),
                 Achievement(slug="wordsmith", name="Wordsmith", description="10,000 total words", icon="📖", trigger_type="total_words", trigger_value="10000"),
                 Achievement(slug="on_a_roll", name="On A Roll", description="7-day usage streak", icon="🔥", trigger_type="streak", trigger_value="7"),
                 Achievement(slug="dedicated", name="Dedicated", description="30-day usage streak", icon="💎", trigger_type="streak", trigger_value="30"),
             ]
             db.add_all(seed_data)
             await db.commit()

# --- Routers ---
app.include_router(auth.router)
app.include_router(stats.router)
app.include_router(achievements.router)
app.include_router(billing.router)
app.include_router(transcribe.router)
app.include_router(transform.router)
app.include_router(writing_prefs.router)
app.include_router(events.router)


@app.get("/")
def read_root():
    return {"status": "ok", "app": "xvoice backend APIs"}

@app.get("/health")
async def health_check():
    """Real dependency check.

    This used to return {"status": "healthy"} unconditionally, which meant uptime
    monitors stayed green while Postgres was unreachable and every request was
    failing. Returns 503 when something the API actually needs is down, so the
    monitor fires before the users do.
    """
    from fastapi.responses import JSONResponse
    from sqlalchemy import text as sql_text
    import time as _time

    checks: dict[str, str] = {}
    healthy = True

    # Database
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(sql_text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
        healthy = False

    # Redis (the transcription queue lives here)
    try:
        from queue_manager import queue_manager
        await queue_manager.redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {type(e).__name__}"
        healthy = False

    # Transcription worker — it stamps a heartbeat each loop. A stale stamp means
    # the workers died while the web process kept serving, which is invisible
    # otherwise: requests just queue up and time out.
    try:
        from queue_manager import queue_manager
        beat = await queue_manager.redis.get("worker:heartbeat")
        if beat is None:
            checks["worker"] = "no heartbeat yet"
        else:
            age = _time.time() - float(beat)
            if age > 120:
                checks["worker"] = f"stale ({int(age)}s)"
                healthy = False
            else:
                checks["worker"] = "ok"
    except Exception as e:
        checks["worker"] = f"error: {type(e).__name__}"

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "degraded",
            "service": "xvoice",
            "checks": checks,
        },
    )
