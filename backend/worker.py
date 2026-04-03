import asyncio
import os
import json
import time
import logging
from openai import AsyncOpenAI
import redis.asyncio as redis
from queue_manager import REDIS_URL, Priority

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
              with open(filepath, "rb") as audio:
                   transcription = await client.audio.transcriptions.create(
                       model="gpt-4o-transcribe",
                       file=audio,
                       language="en"
                   )
              text = transcription.text.strip()
         except Exception as e:
              logger.exception(f"Transcription process error for {filepath}")
              text = f"Error: {e}"

    # Generate metrics
    words = len(text.split())
    chars = len(text)
    duration = time.time() - start_t
    
    # Clean up file
    if os.path.exists(filepath):
         os.remove(filepath)
         
    return {
         "text": text,
         "word_count": words,
         "char_count": chars,
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
            
            
def start_worker():
    asyncio.create_task(worker_loop())
