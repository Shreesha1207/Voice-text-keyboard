import asyncio
import os
import json
import time
import logging
from openai import AsyncOpenAI
import redis.asyncio as redis
from queue_manager import REDIS_URL, Priority
from database import AsyncSessionLocal
from models import User, SubscriptionStatus
from sqlalchemy import select
from datetime import datetime, timedelta
from email_service import send_trial_expired_email

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "sk-mock-key"))
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

logger = logging.getLogger(__name__)

async def process_transcription(job: dict) -> dict:
    start_t = time.time()
    filepath = job.get("filepath")
    
    # Mocking openai logic if no key is provided
    if os.getenv("ENVIRONMENT") == "development" and not os.getenv("OPENAI_API_KEY"):
         await asyncio.sleep(1) # simulate network
         text = "This is a mock transcription because no API key was provided."
    else:
          try:
               lang = job.get("language", "en")
               should_translate = job.get("translate", False)
               
               logger.info(f"Worker Processing - Target Lang: {lang}, Translation Enabled: {should_translate}")
               
               with open(filepath, "rb") as audio:
                    # Step 1: Transcription / Transliteration
                    trans_params = {
                        "model": "gpt-4o-transcribe",
                        "file": audio
                    }
                    
                    if not should_translate:
                         if lang == "en":
                              # Use the transliteration prompt for Trial/English users
                              trans_params["language"] = "en"
                              trans_params["prompt"] = "Transcribe the audio exactly as it sounds. If the speaker is using a language other than English, transliterate those sounds into the Latin (English) alphabet. Do not translate to English meanings.The script should be in English only. "
                         else:
                              # Native language transcription
                              trans_params["language"] = lang

                    logger.info(f"Step 1: Transcribing with params: { {k:v for k,v in trans_params.items() if k != 'file'} }")
                    trans_res = await client.audio.transcriptions.create(**trans_params)
                    source_text = trans_res.text.strip()
                    logger.info(f"Step 1 Result: '{source_text}'")

                    # Step 2: Optional Translation
                    if should_translate:
                         logger.info(f"Step 2: Translating to {lang} via GPT-4o...")
                         chat_res = await client.chat.completions.create(
                             model="gpt-4o",
                             messages=[
                                 {
                                     "role": "system", 
                                     "content": f"Translate the following text into the target language: {lang}. Output only the translated text."
                                 },
                                 {"role": "user", "content": source_text}
                             ]
                         )
                         text = chat_res.choices[0].message.content.strip()
                         logger.info(f"Step 2 Result: '{text}'")
                    else:
                         text = source_text

          except Exception as e:
              logger.exception(f"Transcription process error for {filepath}")
              text = f"Error: {e}"

    # Generate metrics
    words = len(text.split())
    chars = len(text)
    
    # Calculate actual audio duration from WAV file
    import wave
    audio_duration = 0
    try:
        with wave.open(filepath, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                audio_duration = frames / float(rate)
    except Exception:
        pass

    duration = time.time() - start_t
    
    # Clean up file
    if os.path.exists(filepath):
         os.remove(filepath)
         
    return {
         "text": text,
         "word_count": words,
         "char_count": chars,
         "audio_duration": audio_duration,
         "processing_time": duration
    }

async def worker_loop():
    logger.info("Started background transcription worker.")
    while True:
        try:
            # Check PAID queue first
            job_data = await redis_client.lpop(f"queue:{Priority.PAID.name.lower()}")
            
            if not job_data:
                # If PAID is empty, check TRIAL
                job_data = await redis_client.lpop(f"queue:{Priority.TRIAL.name.lower()}")
                
            if job_data:
                job = json.loads(job_data)
                job_id = job["job_id"]
                enqueued_at = job["enqueued_at"]
                
                # Mock processing wait time
                wait_time = asyncio.get_event_loop().time() - enqueued_at
                
                # Process audio
                result = await process_transcription(job)
                result["queue_wait"] = wait_time
                
                # Publish result back
                await redis_client.publish(f"transcribe_result:{job_id}", json.dumps(result))
            else:
                # Nothing in queues
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.exception("Worker iteration error")
            await asyncio.sleep(1)
            
async def trial_cron_loop():
    logger.info("Started background trial expiry checker.")
    while True:
        try:
            async with AsyncSessionLocal() as db:
                # 14 days ago
                cutoff = datetime.utcnow() - timedelta(days=14)
                
                stmt = select(User).where(
                    User.subscription_status == SubscriptionStatus.TRIAL,
                    User.trial_expired_email_sent == False,
                    User.trial_start_at <= cutoff
                )
                result = await db.execute(stmt)
                expired_users = result.scalars().all()
                
                for user in expired_users:
                    success = await asyncio.to_thread(send_trial_expired_email, user.email, user.display_name)
                    if success:
                        user.trial_expired_email_sent = True
                        
                if expired_users:
                    await db.commit()
                    
        except Exception as e:
            logger.exception("Trial cron error")
            
        # Run check every hour
        await asyncio.sleep(3600)
            
def start_worker():
    asyncio.create_task(worker_loop())
    asyncio.create_task(trial_cron_loop())
