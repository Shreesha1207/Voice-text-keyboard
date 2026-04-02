from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from models import Achievement
from database import async_sessionmaker, engine, AsyncSessionLocal
import asyncio
from worker import worker_loop

from routers import auth, stats, achievements, billing, transcribe

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="xvoice API", description="Backend for the Gamified Voice-Text Keyboard App")

# --- Middlewares ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://29629b16-9d9f-4a0b-963b-efdedb055e28.lovableproject.com",
        "https://preview--happy-tiny-glance.lovable.app",
        "https://happy-tiny-glance.lovable.app"
    ], # For production, set to specific origins (Dashboard App)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    logger.info("Initializing database...")
    await init_db()
    
    # Start the worker task that drains Priority Queues
    asyncio.create_task(worker_loop())
    
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


@app.get("/")
def read_root():
    return {"status": "ok", "app": "xvoice backend APIs"}
